# The agents demo (docs/demo-agents.tape), recorded in a container so nothing of the
# machine that records it gets in: user `demo` on host `demo`, home /home/demo, no mounts but
# the sdist in and the output folder out. Built and run by docs/record-demo-agents.sh.
FROM ghcr.io/charmbracelet/vhs:v0.11.0
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
RUN useradd -m -s /bin/bash demo
USER demo
WORKDIR /home/demo
ENV PATH=/home/demo/.local/bin:$PATH UV_PYTHON_PREFERENCE=only-managed SHELL=/bin/bash
COPY --chown=demo py_screen_mux.tar.gz /tmp/sdist/
RUN uv tool install --python 3.14 /tmp/sdist/py_screen_mux.tar.gz && pys --help >/dev/null
COPY --chown=demo claude /home/demo/.local/bin/claude
RUN mkdir -p src/app src/api .config/pys \
    && printf 'ON_CHANGE = ["pys-sessions", "save", "--quiet"]\n' > .config/pys/config.py \
    && printf "PS1='demo:\\\\w\$ '\nHISTFILE=/dev/null\n" >> .bashrc
ENTRYPOINT ["vhs"]
