# syntax=docker/dockerfile:1.7

FROM --platform=linux/amd64 kalilinux/kali-rolling:latest

ARG OSV_SCANNER_VERSION=v2.4.0

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai
ENV PLAYWRIGHT_MCP_BROWSER=chromium
ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/home/kali/cache/playwright
ENV PATH=/home/kali/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

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
    apt-get update; \
    apt-get install -y --no-install-recommends \
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
      chisel-common-binaries adb apktool jadx default-jre-headless libimage-exiftool-perl \
      ripgrep fd-find \
      libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
      libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 \
      libx11-6 libx11-xcb1 libxcb1 libxext6 libxshmfence1 \
      fonts-liberation fonts-noto-color-emoji xdg-utils; \
    ln -sf /usr/bin/fdfind /usr/local/bin/fd; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash kali && \
    echo "kali ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers && \
    mkdir -p /home/kali/workspace /home/kali/reports /home/kali/evidence /home/kali/targets /home/kali/cache /home/kali/tools /home/kali/.local && \
    chown -R kali:kali /home/kali

USER kali

RUN pip3 install --no-cache-dir --break-system-packages \
      pwntools pymongo semgrep pip-audit tccli awscli && \
    sudo npm install -g \
      playwright@latest \
      retire \
      @openai/codex@0.118.0 \
      @anthropic-ai/claude-code@2.1.98 \
      @mariozechner/pi-coding-agent@0.73.0 && \
    cd /tmp && playwright install chromium

RUN cd /tmp && \
    wget -q "https://github.com/projectdiscovery/katana/releases/download/v1.5.0/katana_1.5.0_linux_amd64.zip" -O katana.zip && \
    wget -q "https://github.com/projectdiscovery/nuclei/releases/download/v3.7.1/nuclei_3.7.1_linux_amd64.zip" -O nuclei.zip && \
    wget -q "https://github.com/hahwul/dalfox/releases/download/v2.12.0/dalfox-linux-amd64.tar.gz" -O dalfox.tar.gz && \
    wget -q "https://github.com/BishopFox/cloudfox/releases/download/v2.0.1/cloudfox-linux-amd64.zip" -O cloudfox.zip && \
    wget -q "https://github.com/ropnop/kerbrute/releases/download/v1.0.3/kerbrute_linux_amd64" -O kerbrute && \
    wget -q "https://github.com/google/osv-scanner/releases/download/${OSV_SCANNER_VERSION}/osv-scanner_linux_amd64" -O osv-scanner && \
    git clone --depth 1 "https://github.com/projectdiscovery/nuclei-templates.git" /home/kali/.local/nuclei-templates && \
    unzip -q katana.zip katana && sudo mv katana /usr/local/bin/ && \
    unzip -q nuclei.zip nuclei && sudo mv nuclei /usr/local/bin/ && \
    tar -xzf dalfox.tar.gz && sudo mv dalfox-linux-amd64 /usr/local/bin/dalfox && \
    unzip -q cloudfox.zip cloudfox/cloudfox && sudo mv cloudfox/cloudfox /usr/local/bin/ && \
    sudo cp kerbrute /usr/local/bin/ && \
    sudo cp osv-scanner /usr/local/bin/ && \
    sudo chmod +x /usr/local/bin/kerbrute /usr/local/bin/osv-scanner && \
    nuclei -ut && \
    sudo rm -rf /tmp/*

RUN cd /home/kali/tools && \
    wget -q "https://github.com/frohoff/ysoserial/releases/download/v0.0.6/ysoserial-all.jar" -O ysoserial.jar && \
    git clone --depth=1 https://github.com/ticarpi/jwt_tool.git && \
    cd jwt_tool && pip3 install --no-cache-dir -r requirements.txt --break-system-packages && \
    cd /home/kali/tools && \
    git clone --depth=1 https://github.com/IOActive/jdwp-shellifier.git

RUN sudo bash -c "$(curl -fsSL https://aliyuncli.alicdn.com/install.sh)"

RUN mkdir -p /home/kali/.config/nuclei && \
    echo >> /home/kali/.config/nuclei/config.yaml && \
    echo "disable-update-check: true" >> /home/kali/.config/nuclei/config.yaml && \
    echo "update-template-dir: /home/kali/.local/nuclei-templates" >> /home/kali/.config/nuclei/config.yaml && \
    rm -rf /home/kali/nuclei-templates

RUN printf '%s\n' 'PS1="${PS1%\\n*} \[\033[0;36m\]\D{%y-%m-%d} - \t\[\033[;32m\]\n${PS1##*\\n}"' >> /home/kali/.bashrc && \
    cd /home/kali/workspace && git init

COPY ./.agents /home/kali/workspace/.agents
COPY ./.agents /home/kali/workspace/.claude
COPY ./AGENTS.md /home/kali/workspace/AGENTS.md
COPY ./AGENTS.md /home/kali/workspace/CLAUDE.md

WORKDIR /home/kali/workspace
