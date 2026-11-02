/* _vterm: the "vterm" screen engine, libvterm (deps/libvterm) behind term.Term.
 *
 * libvterm has three layers: the parser (bytes → escape sequences), the state (what
 * the sequences mean: cursor, pen, modes, margins, character sets) and the screen (a
 * store of cells). py-screen uses the first two as they are and keeps its own store:
 *
 *   Term              libvterm's parser + state, and the store below. The engine.
 *   ScreenLayerTerm   libvterm's parser + state + screen, all upstream code. Kept as the
 *                     reference: tests feed both the same bytes and compare every row.
 *
 * Why a store of our own: libvterm's screen keeps the cells in one flat array, so each
 * line feed at the bottom moves every row (screen.c moverect_internal): ~350 KB at
 * 200x50, which made a flood of plain lines no faster than Python. Here the rows are
 * an array of pointers. A full-width scroll rotates the pointers and blanks the rows
 * that come in; other moves (insert or delete characters, left/right margins) copy
 * cells as libvterm does. Everything else mirrors screen.c, so the two stay equal.
 *
 * Scrollback: rows that leave the top of the primary screen go into a ring of `scrollback`
 * rows, under the rule screen.c uses for sb_pushline (a scroll whose region starts at the
 * top-left corner and spans the full width, and the rows a shrink drops from the top),
 * so ScreenLayerTerm, given an sb_pushline that fills the same ring, is still the
 * reference. row(y) with y < 0 reads it: -1 is the newest row. A row keeps its cells up
 * to the last one that shows (a character, a background colour or reverse video).
 *
 * libvterm is compiled into this module from deps/libvterm (its few fixes are listed in
 * deps/README.md). Three
 * private modes the brain needs (?1 cursor keys, ?1006 SGR mouse, ?2004 bracketed
 * paste) have no public getter, so mode() reads VTermState through libvterm's internal
 * header. That is safe because both sides are built from the same source in one
 * build; an update that renames a field fails to compile.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdlib.h>
#include <string.h>

#include "vterm.h"
#include "vterm_internal.h"

#define CONT ((uint32_t)-1)   /* the right half of a wide character, as in screen.c */

typedef struct {
    VTermColor fg, bg;
    unsigned int bold : 1;
    unsigned int underline : 2;
    unsigned int italic : 1;
    unsigned int blink : 1;
    unsigned int reverse : 1;
    unsigned int conceal : 1;
    unsigned int strike : 1;
    unsigned int protected_cell : 1;
} Pen;

typedef struct {
    uint32_t chars[VTERM_MAX_CHARS_PER_CELL];
    Pen pen;
} Cell;

typedef struct {
    Cell **rows;              /* rows[y] → that row's cols cells; scrolling moves these */
    Cell *block;              /* all cells, rows * cols */
} Buffer;

typedef struct {
    Cell *cells;              /* len cells; past them the row is blank */
    int len, cap;
} HistRow;

typedef struct {
    PyObject_HEAD
    VTerm *vt;
    VTermState *state;
    VTermScreen *screen;      /* set only in a ScreenLayerTerm */
    int rows, cols;
    unsigned char *dirty;     /* one byte per row */
    int dirty_cap;
    char *title;              /* UTF-8, NUL-terminated */
    size_t title_len, title_cap;
    char *title_next;         /* fragments of a title still arriving */
    size_t next_len, next_cap;
    /* the store (Term) */
    Buffer buf[2];            /* 0 = primary, 1 = alternate screen */
    int active;
    Pen pen;                  /* the pen new glyphs and erased cells take */
    int global_reverse;       /* DECSCNM */
    /* the scrollback (both types) */
    HistRow *hist;            /* a ring of hist_max rows */
    int hist_max, hist_n, hist_head;   /* hist_head = the oldest row's slot */
    long long pushed;         /* rows ever pushed: the brain anchors a scrolled view on it */
} TermObject;

/* ---- shared helpers ------------------------------------------------------------- */

static void mark_rows(TermObject *t, int start, int end)
{
    if (start < 0)
        start = 0;
    if (end > t->dirty_cap)
        end = t->dirty_cap;
    for (int y = start; y < end; y++)
        t->dirty[y] = 1;
}

static int grow_dirty(TermObject *t, int rows)
{
    if (rows <= t->dirty_cap)
        return 1;
    unsigned char *d = realloc(t->dirty, rows);
    if (!d)
        return 0;
    memset(d + t->dirty_cap, 0, rows - t->dirty_cap);
    t->dirty = d;
    t->dirty_cap = rows;
    return 1;
}

static int append(char **buf, size_t *len, size_t *cap, const char *s, size_t n)
{
    if (*len + n + 1 > *cap) {
        size_t c = (*cap ? *cap : 64);
        while (c < *len + n + 1)
            c *= 2;
        char *b = realloc(*buf, c);
        if (!b)
            return 0;
        *buf = b;
        *cap = c;
    }
    memcpy(*buf + *len, s, n);
    *len += n;
    (*buf)[*len] = '\0';
    return 1;
}

static void take_title(TermObject *t, VTermStringFragment f)
{
    if (f.initial)
        t->next_len = 0;
    append(&t->title_next, &t->next_len, &t->next_cap, f.str, f.len);
    if (f.final) {
        t->title_len = 0;
        append(&t->title, &t->title_len, &t->title_cap,
               t->title_next ? t->title_next : "", t->next_len);
    }
}

static void cb_output(const char *s, size_t len, void *user)
{
    /* Answers to queries (DSR, DA). Not sent to the app yet: a restarted brain
     * replays output, and a replayed query must not be answered twice. */
    (void)s; (void)len; (void)user;
}

/* ---- the store: libvterm state callbacks ------------------------------------------ */

static int buffer_alloc(Buffer *b, int rows, int cols)
{
    b->block = calloc((size_t)rows * cols, sizeof(Cell));
    b->rows = malloc(sizeof(Cell *) * rows);
    if (!b->block || !b->rows) {
        free(b->block);
        free(b->rows);
        b->block = NULL;
        b->rows = NULL;
        return 0;
    }
    for (int y = 0; y < rows; y++)
        b->rows[y] = b->block + (size_t)y * cols;
    return 1;
}

static void buffer_free(Buffer *b)
{
    free(b->block);
    free(b->rows);
    b->block = NULL;
    b->rows = NULL;
}

/* What an erased cell holds: screen.c's erase_internal keeps only the colours. */
static inline Pen erased_pen(const TermObject *t)
{
    Pen p = {0};
    p.fg = t->pen.fg;
    p.bg = t->pen.bg;
    return p;
}

/* ---- the scrollback ------------------------------------------------------------------ */

/* A cell that shows something: a character, a background colour or reverse video.
 * `reverse` is already resolved against DECSCNM. */
static inline int shows(const Cell *c, int reverse)
{
    return c->chars[0] != 0 || reverse || !VTERM_COLOR_IS_DEFAULT_BG(&c->pen.bg);
}

/* The next slot of the ring: a new row while it fills, then the oldest row's slot. */
static HistRow *hist_slot(TermObject *t)
{
    if (t->hist_max <= 0)
        return NULL;
    if (!t->hist) {
        t->hist = calloc((size_t)t->hist_max, sizeof(HistRow));
        if (!t->hist)
            return NULL;
    }
    HistRow *h;
    if (t->hist_n < t->hist_max)
        h = &t->hist[(t->hist_head + t->hist_n++) % t->hist_max];
    else {
        h = &t->hist[t->hist_head];
        t->hist_head = (t->hist_head + 1) % t->hist_max;
    }
    t->pushed++;
    return h;
}

/* Room for n cells in h; 0 when out of memory (the row is then kept empty). */
static int hist_room(HistRow *h, int n)
{
    h->len = 0;
    if (n <= h->cap)
        return 1;
    Cell *c = realloc(h->cells, sizeof(Cell) * (size_t)n);
    if (!c)
        return 0;
    h->cells = c;
    h->cap = n;
    return 1;
}

/* A row of the store leaves the screen. DECSCNM is resolved into each cell, as
 * screen.c's vterm_screen_get_cell does for the cells it pushes. */
static void hist_push_row(TermObject *t, const Cell *row)
{
    if (t->hist_max <= 0)
        return;
    int n = t->cols;
    while (n > 0 && !shows(&row[n - 1], row[n - 1].pen.reverse ^ t->global_reverse))
        n--;
    HistRow *h = hist_slot(t);
    if (!h || !hist_room(h, n))
        return;
    if (n)                            /* a blank row may have no cells allocated yet */
        memcpy(h->cells, row, sizeof(Cell) * (size_t)n);
    for (int x = 0; x < n; x++)
        h->cells[x].pen.reverse ^= t->global_reverse;
    h->len = n;
}

static void hist_clear(TermObject *t)
{
    t->hist_n = 0;
    t->hist_head = 0;
}

static void hist_free(TermObject *t)
{
    if (t->hist)
        for (int i = 0; i < t->hist_max; i++)
            free(t->hist[i].cells);
    free(t->hist);
    t->hist = NULL;
    t->hist_n = t->hist_head = 0;
}

/* Row y < 0 of the scrollback (-1 = the newest), or NULL. */
static const HistRow *hist_row(const TermObject *t, int y)
{
    int i = t->hist_n + y;
    if (y >= 0 || i < 0 || !t->hist)
        return NULL;
    return &t->hist[(t->hist_head + i) % t->hist_max];
}

static int st_putglyph(VTermGlyphInfo *info, VTermPos pos, void *user)
{
    TermObject *t = user;
    if (pos.row < 0 || pos.row >= t->rows || pos.col < 0 || pos.col >= t->cols)
        return 0;
    Cell *row = t->buf[t->active].rows[pos.row];
    Cell *c = &row[pos.col];
    int i;
    for (i = 0; i < VTERM_MAX_CHARS_PER_CELL && info->chars[i]; i++)
        c->chars[i] = info->chars[i];
    if (i < VTERM_MAX_CHARS_PER_CELL)
        c->chars[i] = 0;
    c->pen = t->pen;
    c->pen.protected_cell = info->protected_cell;
    for (int k = 1; k < info->width && pos.col + k < t->cols; k++)
        row[pos.col + k].chars[0] = CONT;
    t->dirty[pos.row] = 1;
    return 1;
}

static int st_erase(VTermRect rect, int selective, void *user)
{
    TermObject *t = user;
    Pen blank = erased_pen(t);
    int end_row = rect.end_row < t->rows ? rect.end_row : t->rows;
    int end_col = rect.end_col < t->cols ? rect.end_col : t->cols;
    for (int y = rect.start_row < 0 ? 0 : rect.start_row; y < end_row; y++) {
        Cell *row = t->buf[t->active].rows[y];
        for (int x = rect.start_col < 0 ? 0 : rect.start_col; x < end_col; x++) {
            if (selective && row[x].pen.protected_cell)
                continue;
            row[x].chars[0] = 0;
            row[x].pen = blank;
        }
        t->dirty[y] = 1;
    }
    return 1;
}

/* Cells of src move to dest (same size), as screen.c's moverect_internal does. */
static int st_moverect(VTermRect dest, VTermRect src, void *user)
{
    TermObject *t = user;
    Cell **rows = t->buf[t->active].rows;
    int cols = src.end_col - src.start_col;
    int downward = src.start_row - dest.start_row;
    int init, test, inc;
    if (downward < 0) {
        init = dest.end_row - 1;
        test = dest.start_row - 1;
        inc = -1;
    } else {
        init = dest.start_row;
        test = dest.end_row;
        inc = +1;
    }
    for (int y = init; y != test; y += inc)
        memmove(&rows[y][dest.start_col], &rows[y + downward][src.start_col],
                (size_t)cols * sizeof(Cell));
    mark_rows(t, dest.start_row, dest.end_row);
    return 1;
}

static int st_scrollrect(VTermRect rect, int downward, int rightward, void *user)
{
    TermObject *t = user;
    int height = rect.end_row - rect.start_row;
    if (rightward == 0 && downward != 0 && abs(downward) < height
            && rect.start_col == 0 && rect.end_col == t->cols) {
        /* Whole rows: rotate the pointers, then blank the rows that came in. The
         * same as vterm_scroll_rect with st_moverect + st_erase, without the copy. */
        Cell **rows = t->buf[t->active].rows;
        int n = abs(downward);
        /* screen.c moverect_internal: rows scrolled off the top-left corner of the
         * primary screen go to the scrollback. Only this path can do it: the other
         * one is never full width, and a scroll of the whole height only erases. */
        if (downward > 0 && rect.start_row == 0 && t->active == 0)
            for (int y = 0; y < n; y++)
                hist_push_row(t, rows[y]);
        Cell *small[64];
        Cell **tmp = n <= 64 ? small : malloc(sizeof(Cell *) * n);
        if (!tmp)
            return 0;
        VTermRect gone = rect;
        if (downward > 0) {           /* content moves up; rows come in at the bottom */
            memcpy(tmp, &rows[rect.start_row], sizeof(Cell *) * n);
            memmove(&rows[rect.start_row], &rows[rect.start_row + n],
                    sizeof(Cell *) * (height - n));
            memcpy(&rows[rect.end_row - n], tmp, sizeof(Cell *) * n);
            gone.start_row = rect.end_row - n;
        } else {                      /* content moves down; rows come in at the top */
            memcpy(tmp, &rows[rect.end_row - n], sizeof(Cell *) * n);
            memmove(&rows[rect.start_row + n], &rows[rect.start_row],
                    sizeof(Cell *) * (height - n));
            memcpy(&rows[rect.start_row], tmp, sizeof(Cell *) * n);
            gone.end_row = rect.start_row + n;
        }
        if (tmp != small)
            free(tmp);
        st_erase(gone, 0, t);
        mark_rows(t, rect.start_row, rect.end_row);
        return 1;
    }
    vterm_scroll_rect(rect, downward, rightward, st_moverect, st_erase, t);
    mark_rows(t, rect.start_row, rect.end_row);
    return 1;
}

static int st_setpenattr(VTermAttr attr, VTermValue *val, void *user)
{
    TermObject *t = user;
    switch (attr) {
    case VTERM_ATTR_BOLD: t->pen.bold = val->boolean; return 1;
    case VTERM_ATTR_UNDERLINE: t->pen.underline = val->number; return 1;
    case VTERM_ATTR_ITALIC: t->pen.italic = val->boolean; return 1;
    case VTERM_ATTR_BLINK: t->pen.blink = val->boolean; return 1;
    case VTERM_ATTR_REVERSE: t->pen.reverse = val->boolean; return 1;
    case VTERM_ATTR_CONCEAL: t->pen.conceal = val->boolean; return 1;
    case VTERM_ATTR_STRIKE: t->pen.strike = val->boolean; return 1;
    case VTERM_ATTR_FOREGROUND: t->pen.fg = val->color; return 1;
    case VTERM_ATTR_BACKGROUND: t->pen.bg = val->color; return 1;
    case VTERM_ATTR_FONT: case VTERM_ATTR_SMALL: case VTERM_ATTR_BASELINE:
        return 1;                     /* kept by libvterm's state; we don't draw them */
    default:
        return 0;
    }
}

static int st_settermprop(VTermProp prop, VTermValue *val, void *user)
{
    TermObject *t = user;
    switch (prop) {
    case VTERM_PROP_TITLE:
        take_title(t, val->string);
        break;
    case VTERM_PROP_ALTSCREEN:
        /* On: the state erases the alternate screen next. Off: the primary comes
         * back as it was, so every row is redrawn. */
        t->active = val->boolean ? 1 : 0;
        if (!val->boolean)
            mark_rows(t, 0, t->rows);
        break;
    case VTERM_PROP_REVERSE:
        t->global_reverse = val->boolean;
        mark_rows(t, 0, t->rows);
        break;
    default:
        break;
    }
    return 1;   /* 0 would make libvterm drop the property (cursor, alt screen...) */
}

/* Cells up to the last one that isn't erased (screen.c line_popcount). */
static int row_width(const Cell *row, int cols)
{
    int x = cols - 1;
    while (x >= 0 && row[x].chars[0] == 0)
        x--;
    return x + 1;
}

/* screen.c's resize_buffer without reflow and without scrollback: more rows → blank
 * rows at the bottom; fewer → first drop blank rows from the bottom, but none at or
 * above the cursor (on the active screen), then rows from the top. A row keeps its
 * cells up to the last one not erased (at most the new width); every other cell is
 * cleared with the current pen, all of it, as screen.c's clearcell does. */
static void resize_buffer(TermObject *t, int b, Buffer nb, int new_rows, int new_cols,
                          VTermPos *cursor)
{
    Buffer *old = &t->buf[b];
    int old_rows = t->rows, old_cols = t->cols;
    int drop_top = 0, keep = old_rows;
    if (new_rows < old_rows) {
        int k = old_rows - new_rows, bottom = 0;
        int floor_row = cursor ? cursor->row : -1;     /* rows above it can't go */
        while (bottom < k && old_rows - 1 - bottom > floor_row
                && row_width(old->rows[old_rows - 1 - bottom], old_cols) == 0)
            bottom++;
        drop_top = k - bottom;
        keep = new_rows;
    }
    if (b == 0)                                         /* screen.c: spare lines */
        for (int y = 0; y < drop_top; y++)
            hist_push_row(t, old->rows[y]);
    Pen blank = t->pen;                                 /* screen.c clearcell */
    for (int y = 0; y < new_rows; y++) {
        Cell *dst = nb.rows[y];
        int x = 0;
        if (y < keep) {
            const Cell *src = old->rows[y + drop_top];
            int n = row_width(src, old_cols);
            if (n > new_cols)
                n = new_cols;
            memcpy(dst, src, sizeof(Cell) * n);
            x = n;
        }
        for (; x < new_cols; x++) {
            dst[x].chars[0] = 0;
            dst[x].pen = blank;
        }
    }
    buffer_free(old);
    *old = nb;
    if (cursor) {
        cursor->row -= drop_top;
        if (cursor->row < 0)
            cursor->row = 0;
        if (cursor->col >= new_cols)
            cursor->col = new_cols - 1;
    }
}

static int st_resize(int rows, int cols, VTermStateFields *fields, void *user)
{
    TermObject *t = user;
    if (!grow_dirty(t, rows))
        return 0;
    /* With a resize callback set, libvterm's state doesn't resize its per-row line
     * info (double width, continuation): the callback must hand back new arrays of
     * `rows` entries, as screen.c does, or the state writes past the old ones. Like
     * screen.c's, they start zeroed (vterm_allocator_malloc zeroes) and the old ones
     * are freed with libvterm's allocator. Everything is allocated first, so a
     * failure changes nothing. */
    Buffer nb[2] = {{0}};
    VTermLineInfo *ni[2] = {NULL, NULL};
    for (int b = 0; b < 2; b++) {
        ni[b] = vterm_allocator_malloc(t->vt, sizeof(VTermLineInfo) * rows);
        if (!ni[b] || !buffer_alloc(&nb[b], rows, cols)) {
            for (int k = 0; k <= b; k++) {
                buffer_free(&nb[k]);
                if (ni[k])
                    vterm_allocator_free(t->vt, ni[k]);
            }
            return 0;
        }
    }
    for (int b = 0; b < 2; b++) {
        resize_buffer(t, b, nb[b], rows, cols, b == t->active ? &fields->pos : NULL);
        if (fields->lineinfos[b])
            vterm_allocator_free(t->vt, fields->lineinfos[b]);
        fields->lineinfos[b] = ni[b];
    }
    t->rows = rows;
    t->cols = cols;
    mark_rows(t, 0, rows);
    return 1;
}

static int st_setlineinfo(int row, const VTermLineInfo *newinfo, const VTermLineInfo *oldinfo,
                          void *user)
{
    /* Double-width lines (DECDWL): screen.c erases the right half of the row. */
    TermObject *t = user;
    if (newinfo->doublewidth && !oldinfo->doublewidth) {
        VTermRect r = {.start_row = row, .end_row = row + 1,
                       .start_col = t->cols / 2, .end_col = t->cols};
        st_erase(r, 0, t);
    }
    if (row >= 0 && row < t->rows)
        t->dirty[row] = 1;
    return 1;
}

static int st_sb_clear(void *user)
{
    /* ED 3 (erase the scrollback). Returns 0, as screen.c does: 1 means "handled" and
     * makes on_csi return early, skipping its tail (the pending-wrap cancel and the
     * cursor update), so the cursor would differ. */
    hist_clear(user);
    return 0;
}

static const VTermStateCallbacks state_cbs = {
    .putglyph = st_putglyph,
    .scrollrect = st_scrollrect,
    .moverect = st_moverect,
    .erase = st_erase,
    .setpenattr = st_setpenattr,
    .settermprop = st_settermprop,
    .resize = st_resize,
    .setlineinfo = st_setlineinfo,
    .sb_clear = st_sb_clear,
};

/* ---- the reference: libvterm's own screen layer ------------------------------------ */

static int sc_damage(VTermRect rect, void *user)
{
    mark_rows((TermObject *)user, rect.start_row, rect.end_row);
    return 1;
}

static int sc_settermprop(VTermProp prop, VTermValue *val, void *user)
{
    if (prop == VTERM_PROP_TITLE)
        take_title((TermObject *)user, val->string);
    return 1;
}

static int sc_resize(int rows, int cols, void *user)
{
    TermObject *t = user;
    t->rows = rows;
    t->cols = cols;
    mark_rows(t, 0, rows);
    return 1;
}

/* The cells screen.c pushes, into the same ring, by the same trimming rule. */
static int sc_sb_pushline(int cols, const VTermScreenCell *cells, void *user)
{
    TermObject *t = user;
    int n = cols;
    while (n > 0 && cells[n - 1].chars[0] == 0 && !cells[n - 1].attrs.reverse
            && VTERM_COLOR_IS_DEFAULT_BG(&cells[n - 1].bg))
        n--;
    HistRow *h = hist_slot(t);
    if (!h || !hist_room(h, n))
        return 1;
    for (int x = 0; x < n; x++) {
        const VTermScreenCell *s = &cells[x];
        Cell *c = &h->cells[x];
        memset(c, 0, sizeof *c);
        for (int i = 0; i < VTERM_MAX_CHARS_PER_CELL && s->chars[i]; i++)
            c->chars[i] = s->chars[i];
        c->pen.fg = s->fg;
        c->pen.bg = s->bg;
        c->pen.bold = s->attrs.bold;
        c->pen.underline = s->attrs.underline;
        c->pen.italic = s->attrs.italic;
        c->pen.blink = s->attrs.blink;
        c->pen.reverse = s->attrs.reverse;
        c->pen.conceal = s->attrs.conceal;
        c->pen.strike = s->attrs.strike;
    }
    h->len = n;
    return 1;
}

static int sc_sb_clear(void *user)
{
    hist_clear(user);
    return 0;               /* as st_sb_clear: let the state finish the sequence */
}

static const VTermScreenCallbacks screen_cbs = {
    .damage = sc_damage,
    .settermprop = sc_settermprop,
    .resize = sc_resize,
    .sb_pushline = sc_sb_pushline,       /* no sb_popline: a taller screen gets blank rows */
    .sb_clear = sc_sb_clear,
};

/* ---- one cell, from either store ------------------------------------------------------ */

static void cell_out(const Cell *c, int width, int reverse, VTermScreenCell *out)
{
    for (int i = 0; i < VTERM_MAX_CHARS_PER_CELL; i++) {
        out->chars[i] = c->chars[i];
        if (!c->chars[i])
            break;
    }
    out->width = width;
    memset(&out->attrs, 0, sizeof out->attrs);
    out->attrs.bold = c->pen.bold;
    out->attrs.underline = c->pen.underline;
    out->attrs.italic = c->pen.italic;
    out->attrs.blink = c->pen.blink;
    out->attrs.reverse = reverse;
    out->attrs.conceal = c->pen.conceal;
    out->attrs.strike = c->pen.strike;
    out->fg = c->pen.fg;
    out->bg = c->pen.bg;
}

static int get_cell(const TermObject *t, int row, int col, VTermScreenCell *out)
{
    if (row < 0) {                    /* the scrollback, the same for both types */
        const HistRow *h = hist_row(t, row);
        if (!h || col < 0 || col >= t->cols)
            return 0;
        if (col >= h->len) {          /* past the row's end: blank, default colours */
            Cell blank;
            memset(&blank, 0, sizeof blank);
            vterm_state_get_default_colors(t->state, &blank.pen.fg, &blank.pen.bg);
            cell_out(&blank, 1, 0, out);
            return 1;
        }
        const Cell *c = &h->cells[col];
        int w = (col + 1 < h->len && h->cells[col + 1].chars[0] == CONT) ? 2 : 1;
        cell_out(c, w, c->pen.reverse, out);
        return 1;
    }
    if (t->screen) {
        VTermPos pos = {.row = row, .col = col};
        return vterm_screen_get_cell(t->screen, pos, out);
    }
    if (row < 0 || row >= t->rows || col < 0 || col >= t->cols)
        return 0;
    Cell *const *rows = t->buf[t->active].rows;
    const Cell *c = &rows[row][col];
    int w = (col + 1 < t->cols && rows[row][col + 1].chars[0] == CONT) ? 2 : 1;
    cell_out(c, w, c->pen.reverse ^ t->global_reverse, out);
    return 1;
}

/* ---- the Python type ---------------------------------------------------------------- */

static int init_common(TermObject *self, PyObject *args, PyObject *kw, int *cols, int *rows)
{
    static char *names[] = {"columns", "lines", "scrollback", NULL};
    int scrollback = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "ii|i", names, cols, rows, &scrollback))
        return -1;
    if (*cols < 1 || *rows < 1) {
        PyErr_SetString(PyExc_ValueError, "columns and lines must be at least 1");
        return -1;
    }
    if (scrollback < 0) {
        PyErr_SetString(PyExc_ValueError, "scrollback must be at least 0");
        return -1;
    }
    if (self->vt) {
        PyErr_SetString(PyExc_RuntimeError, "Term already initialised");
        return -1;
    }
    if (!grow_dirty(self, *rows)) {
        PyErr_NoMemory();
        return -1;
    }
    self->vt = vterm_new(*rows, *cols);
    if (!self->vt) {
        PyErr_NoMemory();
        return -1;
    }
    self->rows = *rows;
    self->cols = *cols;
    self->hist_max = scrollback;      /* the ring itself comes with the first row */
    vterm_set_utf8(self->vt, 1);
    vterm_output_set_callback(self->vt, cb_output, self);
    self->state = vterm_obtain_state(self->vt);
    return 0;
}

static int Term_init(TermObject *self, PyObject *args, PyObject *kw)
{
    int cols, rows;
    if (init_common(self, args, kw, &cols, &rows) < 0)
        return -1;
    if (!buffer_alloc(&self->buf[0], rows, cols) || !buffer_alloc(&self->buf[1], rows, cols)) {
        PyErr_NoMemory();
        return -1;
    }
    vterm_state_set_callbacks(self->state, &state_cbs, self);
    vterm_state_reset(self->state, 1);
    mark_rows(self, 0, rows);
    return 0;
}

static int ScreenLayer_init(TermObject *self, PyObject *args, PyObject *kw)
{
    int cols, rows;
    if (init_common(self, args, kw, &cols, &rows) < 0)
        return -1;
    self->screen = vterm_obtain_screen(self->vt);
    vterm_screen_set_callbacks(self->screen, &screen_cbs, self);
    vterm_screen_enable_altscreen(self->screen, 1);
    vterm_screen_reset(self->screen, 1);
    mark_rows(self, 0, rows);
    return 0;
}

static void Term_dealloc(TermObject *self)
{
    if (self->vt)
        vterm_free(self->vt);
    buffer_free(&self->buf[0]);
    buffer_free(&self->buf[1]);
    hist_free(self);
    free(self->dirty);
    free(self->title);
    free(self->title_next);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

#define READY(self) \
    if (!(self)->vt) { PyErr_SetString(PyExc_RuntimeError, "Term not initialised"); return NULL; }

static PyObject *Term_feed(TermObject *self, PyObject *arg)
{
    READY(self);
    Py_buffer b;
    if (PyObject_GetBuffer(arg, &b, PyBUF_SIMPLE) < 0)
        return NULL;
    const char *p = b.buf;
    size_t left = (size_t)b.len;
    while (left) {                       /* vterm_input_write takes all it is given */
        size_t n = vterm_input_write(self->vt, p, left);
        if (!n)
            break;
        p += n;
        left -= n;
    }
    PyBuffer_Release(&b);
    Py_RETURN_NONE;
}

static PyObject *Term_resize(TermObject *self, PyObject *args)
{
    READY(self);
    int rows, cols;
    if (!PyArg_ParseTuple(args, "ii", &rows, &cols))
        return NULL;
    if (rows < 1 || cols < 1) {
        PyErr_SetString(PyExc_ValueError, "lines and columns must be at least 1");
        return NULL;
    }
    if (!grow_dirty(self, rows))
        return PyErr_NoMemory();
    vterm_set_size(self->vt, rows, cols);
    if (self->rows != rows || self->cols != cols) {   /* the store couldn't allocate */
        PyErr_NoMemory();
        return NULL;
    }
    mark_rows(self, 0, rows);
    Py_RETURN_NONE;
}

static PyObject *Term_take_dirty(TermObject *self, PyObject *Py_UNUSED(ignored))
{
    READY(self);
    PyObject *out = PySet_New(NULL);
    if (!out)
        return NULL;
    int n = self->rows < self->dirty_cap ? self->rows : self->dirty_cap;
    for (int y = 0; y < n; y++) {
        if (!self->dirty[y])
            continue;
        PyObject *v = PyLong_FromLong(y);
        if (!v || PySet_Add(out, v) < 0) {
            Py_XDECREF(v);
            Py_DECREF(out);
            return NULL;
        }
        Py_DECREF(v);
    }
    memset(self->dirty, 0, self->dirty_cap);   /* rows past a shrink, too */
    return out;
}

/* ---- rendering a row ------------------------------------------------------------------ */

typedef struct {
    char *s;
    size_t len, cap;
    int oom;
} Buf;

static void put(Buf *b, const char *s, size_t n)
{
    if (!b->oom && !append(&b->s, &b->len, &b->cap, s, n))
        b->oom = 1;
}

static void put_utf8(Buf *b, uint32_t c)
{
    char u[4];
    size_t n;
    if (c < 0x80) { u[0] = (char)c; n = 1; }
    else if (c < 0x800) { u[0] = (char)(0xC0 | (c >> 6)); u[1] = (char)(0x80 | (c & 0x3F)); n = 2; }
    else if (c < 0x10000) {
        if (c >= 0xD800 && c < 0xE000)
            c = 0xFFFD;
        u[0] = (char)(0xE0 | (c >> 12)); u[1] = (char)(0x80 | ((c >> 6) & 0x3F));
        u[2] = (char)(0x80 | (c & 0x3F)); n = 3;
    } else if (c < 0x110000) {
        u[0] = (char)(0xF0 | (c >> 18)); u[1] = (char)(0x80 | ((c >> 12) & 0x3F));
        u[2] = (char)(0x80 | ((c >> 6) & 0x3F)); u[3] = (char)(0x80 | (c & 0x3F)); n = 4;
    } else { u[0] = (char)0xEF; u[1] = (char)0xBF; u[2] = (char)0xBD; n = 3; }
    put(b, u, n);
}

static void put_color(Buf *b, const VTermColor *c, int base)
{
    /* base 30 = foreground, 40 = background */
    char s[24];
    int n;
    if (base == 30 ? VTERM_COLOR_IS_DEFAULT_FG(c) : VTERM_COLOR_IS_DEFAULT_BG(c))
        n = snprintf(s, sizeof s, ";%d", base + 9);
    else if (VTERM_COLOR_IS_INDEXED(c)) {
        int i = c->indexed.idx;
        if (i < 8)
            n = snprintf(s, sizeof s, ";%d", base + i);
        else if (i < 16)
            n = snprintf(s, sizeof s, ";%d", base + 60 + i - 8);
        else
            n = snprintf(s, sizeof s, ";%d;5;%d", base + 8, i);
    } else
        n = snprintf(s, sizeof s, ";%d;2;%d;%d;%d", base + 8,
                     c->rgb.red, c->rgb.green, c->rgb.blue);
    put(b, s, (size_t)n);
}

static int same_pen(const VTermScreenCell *a, const VTermScreenCell *b)
{
    return a->attrs.bold == b->attrs.bold && a->attrs.underline == b->attrs.underline
        && a->attrs.italic == b->attrs.italic && a->attrs.blink == b->attrs.blink
        && a->attrs.reverse == b->attrs.reverse && a->attrs.conceal == b->attrs.conceal
        && a->attrs.strike == b->attrs.strike
        && vterm_color_is_equal(&a->fg, &b->fg) && vterm_color_is_equal(&a->bg, &b->bg);
}

static void put_pen(Buf *b, const VTermScreenCell *c)
{
    put(b, "\x1b[0", 3);
    put_color(b, &c->fg, 30);
    put_color(b, &c->bg, 40);
    if (c->attrs.bold) put(b, ";1", 2);
    if (c->attrs.italic) put(b, ";3", 2);
    if (c->attrs.underline) put(b, ";4", 2);
    if (c->attrs.blink) put(b, ";5", 2);
    if (c->attrs.reverse) put(b, ";7", 2);
    if (c->attrs.conceal) put(b, ";8", 2);
    if (c->attrs.strike) put(b, ";9", 2);
    put(b, "m", 1);
}

/* Row y as text; with pens = 1, as SGR + text ending in a reset. Exactly `width`
 * cells: a wide character that would cross it, or whose right half is gone (a cut
 * by a resize, an overwrite), becomes spaces, so a row never runs past its pane. */
static PyObject *render(TermObject *self, int y, int width, int pens)
{
    Buf b = {0};
    if (width > self->cols)
        width = self->cols;
    VTermScreenCell cell, last;
    int have_last = 0;
    if (y >= -self->hist_n && y < self->rows) {
        for (int x = 0; x < width;) {
            if (!get_cell(self, y, x, &cell))
                break;
            int w = cell.width > 0 ? cell.width : 1;
            uint32_t c0 = cell.chars[0];
            int blank = c0 == 0 || c0 == CONT || x + w > width
                     || (w == 1 && vterm_unicode_width(c0) > 1);
            if (pens && (!have_last || !same_pen(&cell, &last))) {
                put_pen(&b, &cell);
                last = cell;
                have_last = 1;
            }
            if (blank) {
                for (int i = 0; i < w && x + i < width; i++)
                    put(&b, " ", 1);
            } else {
                for (int i = 0; i < VTERM_MAX_CHARS_PER_CELL && cell.chars[i]; i++)
                    put_utf8(&b, cell.chars[i]);
            }
            x += w;
        }
    }
    if (pens)
        put(&b, "\x1b[0m", 4);
    if (b.oom) {
        free(b.s);
        return PyErr_NoMemory();
    }
    PyObject *s = PyUnicode_DecodeUTF8(b.s ? b.s : "", (Py_ssize_t)b.len, "replace");
    free(b.s);
    return s;
}

static PyObject *Term_row(TermObject *self, PyObject *args)
{
    READY(self);
    int y, width;
    if (!PyArg_ParseTuple(args, "ii", &y, &width))
        return NULL;
    return render(self, y, width, 1);
}

static PyObject *Term_mode(TermObject *self, PyObject *arg)
{
    READY(self);
    long n = PyLong_AsLong(arg);
    if (n == -1 && PyErr_Occurred())
        return NULL;
    const VTermState *s = self->state;
    int on;
    switch (n) {
    case 1:    on = s->mode.cursor; break;
    case 25:   on = s->mode.cursor_visible; break;
    case 1000: on = (s->mouse_flags & MOUSE_WANT_CLICK) != 0; break;
    case 1002: on = (s->mouse_flags & MOUSE_WANT_DRAG) != 0; break;
    case 1003: on = (s->mouse_flags & MOUSE_WANT_MOVE) != 0; break;
    case 1005: on = s->mouse_protocol == MOUSE_UTF8; break;
    case 1006: on = s->mouse_protocol == MOUSE_SGR; break;
    case 1015: on = s->mouse_protocol == MOUSE_RXVT; break;
    case 47: case 1047: case 1049: on = s->mode.alt_screen; break;
    case 2004: on = s->mode.bracketpaste; break;
    default:   on = 0;
    }
    return PyBool_FromLong(on);
}

static PyObject *Term_get_display(TermObject *self, void *closure)
{
    READY(self);
    PyObject *out = PyList_New(self->rows);
    if (!out)
        return NULL;
    for (int y = 0; y < self->rows; y++) {
        PyObject *s = render(self, y, self->cols, 0);
        if (!s) {
            Py_DECREF(out);
            return NULL;
        }
        PyList_SET_ITEM(out, y, s);
    }
    return out;
}

static PyObject *Term_get_cursor(TermObject *self, void *closure)
{
    READY(self);
    VTermPos p;
    vterm_state_get_cursorpos(self->state, &p);
    return Py_BuildValue("(ii)", p.col, p.row);
}

static PyObject *Term_get_cursor_hidden(TermObject *self, void *closure)
{
    READY(self);
    return PyBool_FromLong(!self->state->mode.cursor_visible);
}

static PyObject *Term_get_in_alt(TermObject *self, void *closure)
{
    READY(self);
    return PyBool_FromLong(self->state->mode.alt_screen);
}

static PyObject *Term_get_title(TermObject *self, void *closure)
{
    return PyUnicode_DecodeUTF8(self->title ? self->title : "", (Py_ssize_t)self->title_len,
                                "replace");
}

static PyObject *Term_get_columns(TermObject *self, void *closure)
{
    return PyLong_FromLong(self->cols);
}

static PyObject *Term_get_lines(TermObject *self, void *closure)
{
    return PyLong_FromLong(self->rows);
}

static PyObject *Term_get_history(TermObject *self, void *closure)
{
    return PyLong_FromLong(self->hist_n);
}

static PyObject *Term_get_pushed(TermObject *self, void *closure)
{
    return PyLong_FromLongLong(self->pushed);
}

static PyMethodDef Term_methods[] = {
    {"feed", (PyCFunction)Term_feed, METH_O, "Parse bytes the app wrote."},
    {"resize", (PyCFunction)Term_resize, METH_VARARGS, "resize(lines, columns)"},
    {"take_dirty", (PyCFunction)Term_take_dirty, METH_NOARGS,
     "The rows changed since the last call, as a set; clears them."},
    {"row", (PyCFunction)Term_row, METH_VARARGS,
     "row(y, width): row y in exactly width cells, as SGR + text, ending in a reset;\n"
     "y < 0 reads the scrollback (-1 = the newest row)."},
    {"mode", (PyCFunction)Term_mode, METH_O, "mode(n): private mode n (DECSET n) is on."},
    {NULL}
};

static PyGetSetDef Term_getset[] = {
    {"display", (getter)Term_get_display, NULL, "Every row as plain text.", NULL},
    {"cursor", (getter)Term_get_cursor, NULL, "(x, y), 0-based.", NULL},
    {"cursor_hidden", (getter)Term_get_cursor_hidden, NULL, NULL, NULL},
    {"in_alt", (getter)Term_get_in_alt, NULL, "The alternate screen is on.", NULL},
    {"title", (getter)Term_get_title, NULL, "The app's window title (OSC 0 / 2).", NULL},
    {"columns", (getter)Term_get_columns, NULL, NULL, NULL},
    {"lines", (getter)Term_get_lines, NULL, NULL, NULL},
    {"history", (getter)Term_get_history, NULL,
     "Rows in the scrollback: row(y, width) reads them for -history <= y < 0.", NULL},
    {"pushed", (getter)Term_get_pushed, NULL,
     "Rows ever pushed into the scrollback (it counts on when the ring is full).", NULL},
    {NULL}
};

static PyTypeObject TermType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "py_screen._vterm.Term",
    .tp_basicsize = sizeof(TermObject),
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = "A screen: libvterm's parser and state, py-screen's store.\n"
              "Term(columns, lines, scrollback=0).",
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Term_init,
    .tp_dealloc = (destructor)Term_dealloc,
    .tp_methods = Term_methods,
    .tp_getset = Term_getset,
};

static PyTypeObject ScreenLayerType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "py_screen._vterm.ScreenLayerTerm",
    .tp_basicsize = sizeof(TermObject),
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = "libvterm's own screen layer, the reference for tests.\n"
              "ScreenLayerTerm(columns, lines, scrollback=0).",
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)ScreenLayer_init,
    .tp_dealloc = (destructor)Term_dealloc,
    .tp_methods = Term_methods,
    .tp_getset = Term_getset,
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_vterm",
    .m_doc = "libvterm, the vterm screen engine.",
    .m_size = -1,
};

static int add_type(PyObject *m, const char *name, PyTypeObject *type)
{
    if (PyType_Ready(type) < 0)
        return -1;
    Py_INCREF(type);
    if (PyModule_AddObject(m, name, (PyObject *)type) < 0) {
        Py_DECREF(type);
        return -1;
    }
    return 0;
}

PyMODINIT_FUNC PyInit__vterm(void)
{
    PyObject *m = PyModule_Create(&module);
    if (!m)
        return NULL;
    if (add_type(m, "Term", &TermType) < 0 || add_type(m, "ScreenLayerTerm", &ScreenLayerType) < 0
            || PyModule_AddStringConstant(m, "LIBVTERM_VERSION", "0.3.3") < 0) {
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
