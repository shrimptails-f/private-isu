FROM node:24-bookworm-slim AS node
FROM golang:1.27.4-bookworm
COPY --from=node /usr/local/ /usr/local/
ARG TASK_VERSION=v3.45.4
ENV TZ=Asia/Tokyo GOPATH=/home/dev/go NPM_CONFIG_PREFIX=/home/dev/.local PATH=/home/dev/.local/bin:/home/dev/go/bin:${PATH}
RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates curl git gh ripgrep unzip bzip2 make default-mysql-client tzdata && rm -rf /var/lib/apt/lists/* \
 && arch="$(dpkg --print-architecture)" && curl -fsSL "https://github.com/go-task/task/releases/download/${TASK_VERSION}/task_linux_${arch}.tar.gz" | tar -xz -C /usr/local/bin task \
 && groupadd -g 1000 dev && useradd -m -u 1000 -g 1000 -s /bin/bash dev \
 && mkdir -p /home/dev/.local /home/dev/go/pkg/mod && chown -R dev:dev /home/dev
COPY --chown=dev:dev .devcontainer/bashrc /home/dev/.bashrc
RUN printf "source ~/.bashrc\n" > /home/dev/.bash_profile && chown dev:dev /home/dev/.bash_profile
COPY scripts/gh-wrappers.bash /usr/local/share/gh-wrappers.bash
USER dev
RUN npm install -g @openai/codex@latest && curl -fsSL https://claude.ai/install.sh -o /tmp/install-claude.sh && bash /tmp/install-claude.sh && rm /tmp/install-claude.sh && task --version && codex --version && claude --version
WORKDIR /workspace/private-isu
