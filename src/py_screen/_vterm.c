/* _vterm: the "vterm" screen engine, libvterm (deps/libvterm) behind term.Term.
 *
 * One Term per window. feed() runs the bytes through libvterm's parser, state and
 * screen layers, all in C; the brain then asks for what it draws: the rows that
 * changed (take_dirty), a row as SGR + text (row), the cursor, the modes, the title.
 *
 * libvterm is compiled into this module from the same source tree, unpatched. Three
 * private modes the brain needs (?1 cursor keys, ?1006 SGR mouse, ?2004 bracketed
 * paste) have no public getter, so mode() reads them from VTermState through
 * libvterm's internal header. That is safe because both sides are built from the
 * same source in one build; an update that renames a field fails to compile.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdlib.h>
#include <string.h>

#include "vterm.h"
#include "vterm_internal.h"

typedef struct {
    PyObject_HEAD
    VTerm *vt;
    VTermScreen *screen;
    VTermState *state;
    int rows, cols;
    unsigned char *dirty;     /* one byte per row */
    int dirty_cap;
    char *title;              /* UTF-8, NUL-terminated */
    size_t title_len, title_cap;
    char *title_next;         /* fragments of a title still arriving */
    size_t next_len, next_cap;
} TermObject;

/* ---- libvterm callbacks ------------------------------------------------------ */

static void mark_rows(TermObject *t, int start, int end)
{
    if (start < 0)
        start = 0;
    if (end > t->dirty_cap)
        end = t->dirty_cap;
    for (int y = start; y < end; y++)
        t->dirty[y] = 1;
}

static int cb_damage(VTermRect rect, void *user)
{
    mark_rows((TermObject *)user, rect.start_row, rect.end_row);
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

static int cb_settermprop(VTermProp prop, VTermValue *val, void *user)
{
    TermObject *t = user;
    if (prop == VTERM_PROP_TITLE) {
        VTermStringFragment f = val->string;
        if (f.initial)
            t->next_len = 0;
        append(&t->title_next, &t->next_len, &t->next_cap, f.str, f.len);
        if (f.final) {
            t->title_len = 0;
            append(&t->title, &t->title_len, &t->title_cap,
                   t->title_next ? t->title_next : "", t->next_len);
        }
    }
    /* 1 = accepted: returning 0 would make libvterm drop the property, so the
     * cursor would never hide and the alternate screen never switch. */
    return 1;
}

static int cb_resize(int rows, int cols, void *user)
{
    TermObject *t = user;
    t->rows = rows;
    t->cols = cols;
    mark_rows(t, 0, rows);
    return 1;
}

static void cb_output(const char *s, size_t len, void *user)
{
    /* Answers to queries (DSR, DA). Not sent to the app yet: a restarted brain
     * replays output, and a replayed query must not be answered twice. */
    (void)s; (void)len; (void)user;
}

static const VTermScreenCallbacks screen_cbs = {
    .damage = cb_damage,
    .settermprop = cb_settermprop,
    .resize = cb_resize,
};

/* ---- type ------------------------------------------------------------------- */

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

static int Term_init(TermObject *self, PyObject *args, PyObject *kw)
{
    static char *names[] = {"columns", "lines", NULL};
    int cols, rows;
    if (!PyArg_ParseTupleAndKeywords(args, kw, "ii", names, &cols, &rows))
        return -1;
    if (cols < 1 || rows < 1) {
        PyErr_SetString(PyExc_ValueError, "columns and lines must be at least 1");
        return -1;
    }
    if (self->vt) {
        PyErr_SetString(PyExc_RuntimeError, "Term already initialised");
        return -1;
    }
    if (!grow_dirty(self, rows)) {
        PyErr_NoMemory();
        return -1;
    }
    self->vt = vterm_new(rows, cols);
    if (!self->vt) {
        PyErr_NoMemory();
        return -1;
    }
    self->rows = rows;
    self->cols = cols;
    vterm_set_utf8(self->vt, 1);
    vterm_output_set_callback(self->vt, cb_output, self);
    self->screen = vterm_obtain_screen(self->vt);
    self->state = vterm_obtain_state(self->vt);
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
    self->rows = rows;
    self->cols = cols;
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
        self->dirty[y] = 0;
    }
    memset(self->dirty, 0, self->dirty_cap);   /* rows past a shrink, too */
    return out;
}

/* ---- rendering a row ---------------------------------------------------------- */

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

/* Row y as text; with pens = 1, as SGR + text ending in a reset. A wide character
 * that would cross `width` becomes a space, so the row never runs past it. */
static PyObject *render(TermObject *self, int y, int width, int pens)
{
    Buf b = {0};
    if (width > self->cols)
        width = self->cols;
    VTermScreenCell cell, last;
    int have_last = 0;
    if (y >= 0 && y < self->rows) {
        for (int x = 0; x < width;) {
            VTermPos pos = {.row = y, .col = x};
            if (!vterm_screen_get_cell(self->screen, pos, &cell))
                break;
            int w = cell.width > 0 ? cell.width : 1;
            if (pens && (!have_last || !same_pen(&cell, &last))) {
                put_pen(&b, &cell);
                last = cell;
                have_last = 1;
            }
            if (x + w > width || cell.chars[0] == 0 || cell.chars[0] == (uint32_t)-1) {
                for (int i = 0; i < (x + w > width ? width - x : w); i++)
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

static PyMethodDef Term_methods[] = {
    {"feed", (PyCFunction)Term_feed, METH_O, "Parse bytes the app wrote."},
    {"resize", (PyCFunction)Term_resize, METH_VARARGS, "resize(lines, columns)"},
    {"take_dirty", (PyCFunction)Term_take_dirty, METH_NOARGS,
     "The rows changed since the last call, as a set; clears them."},
    {"row", (PyCFunction)Term_row, METH_VARARGS,
     "row(y, width): row y cut to width cells, as SGR + text, ending in a reset."},
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
    {NULL}
};

static PyTypeObject TermType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "py_screen._vterm.Term",
    .tp_basicsize = sizeof(TermObject),
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = "A screen kept by libvterm: Term(columns, lines).",
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Term_init,
    .tp_dealloc = (destructor)Term_dealloc,
    .tp_methods = Term_methods,
    .tp_getset = Term_getset,
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_vterm", "libvterm, the vterm screen engine.", -1, NULL,
};

PyMODINIT_FUNC PyInit__vterm(void)
{
    if (PyType_Ready(&TermType) < 0)
        return NULL;
    PyObject *m = PyModule_Create(&module);
    if (!m)
        return NULL;
    Py_INCREF(&TermType);
    if (PyModule_AddObject(m, "Term", (PyObject *)&TermType) < 0) {
        Py_DECREF(&TermType);
        Py_DECREF(m);
        return NULL;
    }
    PyModule_AddStringConstant(m, "LIBVTERM_VERSION", "0.3.3");
    return m;
}
