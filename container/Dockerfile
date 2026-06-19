# syntax=docker/dockerfile:1.7

FROM --platform=linux/amd64 kalilinux/kali-last-release:latest

ARG KALI_MIRROR=http://kali.download/kali

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    printf '%s\n' \
      'Acquire::Retries "30";' \
      'Acquire::http::Timeout "180";' \
      'Acquire::https::Timeout "180";' \
      'Acquire::http::Pipeline-Depth "0";' \
      'Acquire::Queue-Mode "access";' \
      'Acquire::Languages "none";' \
      'APT::Keep-Downloaded-Packages "true";' \
      'Binary::apt::APT::Keep-Downloaded-Packages "true";' \
      > /etc/apt/apt.conf.d/80-network-retries; \
    echo "deb ${KALI_MIRROR} kali-last-snapshot main contrib non-free non-free-firmware" > /etc/apt/sources.list; \
    rm -rf /etc/apt/sources.list.d/* /var/lib/apt/lists/*; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl wget git sudo zsh tmux vim-tiny less file \
      tzdata locales \
      unzip zip tar gzip xz-utils p7zip-full \
      jq yq bsdextrautils procps psmisc tini \
      gnupg dirmngr lsof tree rsync xxd rlwrap \
      python3 python3-pip python3-venv pipx \
      nodejs npm \
      make gcc g++ pkg-config \
      iproute2 iputils-ping bind9-dnsutils whois traceroute openssl \
      ncat socat tcpdump proxychains4 openssh-client sshpass \
      nmap whatweb wafw00f ffuf gobuster feroxbuster dirsearch nikto sqlmap \
      dnsrecon dnsenum amass theharvester naabu gitleaks \
      impacket-scripts netexec certipy-ad bloodyad coercer \
      enum4linux-ng krb5-user smbclient samba-common-bin ldap-utils \
      chisel-common-binaries adb default-jre-headless libimage-exiftool-perl \
      ripgrep fd-find \
      libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
      libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 \
      libx11-6 libx11-xcb1 libxcb1 libxext6 libxshmfence1 \
      fonts-liberation fonts-noto-color-emoji xdg-utils; \
    ln -sf /usr/bin/fdfind /usr/local/bin/fd

RUN useradd -m -s /bin/bash kali && \
    echo "kali ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers
USER kali

RUN pip3 install pwntools pymongo --break-system-packages && \
    sudo npm install -g @playwright/cli@latest && \
    cd /tmp && playwright-cli install

RUN mkdir -p /home/kali/.local && \
    cd /tmp && \
    wget -q "https://github.com/projectdiscovery/katana/releases/download/v1.5.0/katana_1.5.0_linux_amd64.zip" -O katana.zip && \
    wget -q "https://github.com/projectdiscovery/nuclei/releases/download/v3.7.1/nuclei_3.7.1_linux_amd64.zip" -O nuclei.zip && \
    wget -q "https://github.com/hahwul/dalfox/releases/download/v2.12.0/dalfox-linux-amd64.tar.gz" -O dalfox.tar.gz && \
    wget -q "https://github.com/BishopFox/cloudfox/releases/download/v2.0.1/cloudfox-linux-amd64.zip" -O cloudfox.zip && \
    wget -q "https://github.com/ropnop/kerbrute/releases/download/v1.0.3/kerbrute_linux_amd64" -O kerbrute && \
    git clone --depth 1 "https://github.com/projectdiscovery/nuclei-templates.git" /home/kali/.local/nuclei-templates && \
    unzip -q katana.zip katana && sudo mv katana /usr/local/bin/ && \
    unzip -q nuclei.zip nuclei && sudo mv nuclei /usr/local/bin/ && \
    tar -xzf dalfox.tar.gz && sudo mv dalfox-linux-amd64 /usr/local/bin/dalfox && \
    unzip -q cloudfox.zip cloudfox/cloudfox && sudo mv cloudfox/cloudfox /usr/local/bin/ && \
    sudo cp kerbrute /usr/local/bin/ && \
    nuclei -ut && \
    sudo rm -rf /tmp/*

RUN mkdir -p /home/kali/knowledges && cd /home/kali/knowledges && \
    git clone --depth=1 https://github.com/swisskyrepo/InternalAllTheThings.git && \
    git clone --depth=1 https://github.com/HackTricks-wiki/hacktricks.git && \
    git clone --depth=1 https://github.com/HackTricks-wiki/hacktricks-cloud.git

RUN mkdir -p /home/kali/tools && cd /home/kali/tools && \
    wget -q "https://github.com/frohoff/ysoserial/releases/download/v0.0.6/ysoserial-all.jar" -O ysoserial.jar && \
    git clone --depth=1 https://github.com/ticarpi/jwt_tool.git && \
    cd jwt_tool && pip3 install -r requirements.txt --break-system-packages && \
    git clone --depth=1 https://github.com/IOActive/jdwp-shellifier.git

RUN pip3 install tccli awscli --break-system-packages && \
    sudo bash -c "$(curl -fsSL https://aliyuncli.alicdn.com/install.sh)"

RUN sudo npm install -g @openai/codex@0.118.0
RUN sudo npm install -g @anthropic-ai/claude-code@2.1.98
RUN sudo npm install -g @mariozechner/pi-coding-agent@0.73.0

RUN sudo chmod +x /usr/local/bin/kerbrute
RUN mkdir -p /home/kali/.config/nuclei && \
    echo >> /home/kali/.config/nuclei/config.yaml && \
    echo "disable-update-check: true" >> /home/kali/.config/nuclei/config.yaml && \
    echo "update-template-dir: /home/kali/.local/nuclei-templates" >> /home/kali/.config/nuclei/config.yaml && \
    rm -rf /home/kali/nuclei-templates
RUN sudo rm -rf /usr/lib/llvm-*/build && \
    for path in /usr/lib/mysql/plugin/auth_pam_tool_dir /etc/ssl/private; do \
      if [ -e "$path" ]; then sudo chmod o+rx "$path"; fi; \
    done
RUN printf '%s\n' 'PS1="${PS1%\\n*} \[\033[0;36m\]\D{%y-%m-%d} - \t\[\033[;32m\]\n${PS1##*\\n}"' >> /home/kali/.bashrc && \
    mkdir -p /home/kali/workspace && cd /home/kali/workspace && git init
ENV PLAYWRIGHT_MCP_BROWSER=chromium
ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
ENV PYTHONUNBUFFERED=1

COPY ./.agents /home/kali/workspace/.agents
COPY ./.agents /home/kali/workspace/.claude
COPY ./AGENTS.md /home/kali/workspace/AGENTS.md
COPY ./AGENTS.md /home/kali/workspace/CLAUDE.md

WORKDIR /home/kali/workspace
