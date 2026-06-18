FROM --platform=amd64 kalilinux/kali-rolling:latest

ARG http_proxy
ARG https_proxy
ARG no_proxy

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai
ENV PLAYWRIGHT_MCP_BROWSER=chromium
ENV CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
ENV PYTHONUNBUFFERED=1

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" && \
    if [[ -n "${http_proxy}" || -n "${https_proxy}" ]]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\nAcquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' \
            "${http_proxy}" "${https_proxy}" > /etc/apt/apt.conf.d/99proxy; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends kali-linux-headless && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99proxy

RUN useradd -m -s /bin/bash kali && \
    echo "kali ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER kali

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" && \
    if [[ -n "${http_proxy}" || -n "${https_proxy}" ]]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\nAcquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' \
            "${http_proxy}" "${https_proxy}" | sudo tee /etc/apt/apt.conf.d/99proxy > /dev/null; \
    fi && \
    sudo apt-get update && \
    sudo apt-get install -y --no-install-recommends \
        bsdextrautils \
        npm \
        jq \
        iputils-ping \
        sshpass \
        ncat \
        rlwrap \
        bloodyad \
        coercer \
        enum4linux-ng \
        pwncat \
        chisel-common-binaries \
        dirsearch \
        yq \
        krb5-user \
        gitleaks \
        naabu \
        nikto \
        netexec \
        adb && \
    sudo apt-get clean && \
    sudo rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99proxy

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" PIP_DISABLE_PIP_VERSION_CHECK=1 && \
    pip3 install --retries 5 --timeout 120 --break-system-packages pwntools pymongo

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" \
        npm_config_fetch_retries=5 npm_config_proxy="${http_proxy}" npm_config_https_proxy="${https_proxy}" && \
    sudo npm install -g @playwright/cli@latest && \
    cd /tmp && playwright-cli install

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" GIT_HTTP_VERSION=HTTP/1.1 && \
    git config --global http.version HTTP/1.1 && \
    git config --global http.lowSpeedLimit 1000 && \
    git config --global http.lowSpeedTime 60 && \
    mkdir -p /home/kali/.local && \
    cd /tmp && \
    wget -t 5 -T 30 --waitretry=5 -q "https://github.com/projectdiscovery/katana/releases/download/v1.5.0/katana_1.5.0_linux_amd64.zip" -O katana.zip && \
    wget -t 5 -T 30 --waitretry=5 -q "https://github.com/projectdiscovery/nuclei/releases/download/v3.7.1/nuclei_3.7.1_linux_amd64.zip" -O nuclei.zip && \
    wget -t 5 -T 30 --waitretry=5 -q "https://github.com/hahwul/dalfox/releases/download/v2.12.0/dalfox-linux-amd64.tar.gz" -O dalfox.tar.gz && \
    wget -t 5 -T 30 --waitretry=5 -q "https://github.com/BishopFox/cloudfox/releases/download/v2.0.1/cloudfox-linux-amd64.zip" -O cloudfox.zip && \
    wget -t 5 -T 30 --waitretry=5 -q "https://github.com/ropnop/kerbrute/releases/download/v1.0.3/kerbrute_linux_amd64" -O kerbrute && \
    git clone --depth 1 "https://github.com/projectdiscovery/nuclei-templates.git" /home/kali/.local/nuclei-templates && \
    unzip -q katana.zip katana && sudo mv katana /usr/local/bin/ && \
    unzip -q nuclei.zip nuclei && sudo mv nuclei /usr/local/bin/ && \
    tar -xzf dalfox.tar.gz && sudo mv dalfox-linux-amd64 /usr/local/bin/dalfox && \
    unzip -q cloudfox.zip cloudfox/cloudfox && sudo mv cloudfox/cloudfox /usr/local/bin/ && \
    sudo cp kerbrute /usr/local/bin/ && \
    nuclei -ut && \
    sudo rm -rf /tmp/*

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" GIT_HTTP_VERSION=HTTP/1.1 && \
    mkdir -p /home/kali/knowledges && \
    cd /home/kali/knowledges && \
    git clone --depth=1 https://github.com/swisskyrepo/InternalAllTheThings.git && \
    git clone --depth=1 https://github.com/HackTricks-wiki/hacktricks.git && \
    git clone --depth=1 https://github.com/HackTricks-wiki/hacktricks-cloud.git

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" GIT_HTTP_VERSION=HTTP/1.1 \
        PIP_DISABLE_PIP_VERSION_CHECK=1 && \
    mkdir -p /home/kali/tools && \
    cd /home/kali/tools && \
    wget -t 5 -T 30 --waitretry=5 -q "https://github.com/frohoff/ysoserial/releases/download/v0.0.6/ysoserial-all.jar" -O ysoserial.jar && \
    git clone --depth=1 https://github.com/ticarpi/jwt_tool.git && \
    cd jwt_tool && pip3 install --retries 5 --timeout 120 --break-system-packages -r requirements.txt && \
    cd /home/kali/tools && \
    git clone --depth=1 https://github.com/IOActive/jdwp-shellifier.git

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" && \
    if [[ -n "${http_proxy}" || -n "${https_proxy}" ]]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\nAcquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' \
            "${http_proxy}" "${https_proxy}" | sudo tee /etc/apt/apt.conf.d/99proxy > /dev/null; \
    fi && \
    cd /tmp && \
    wget -t 5 -T 30 --waitretry=5 -q "https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/ripgrep_15.1.0-1_amd64.deb" -O ripgrep.deb && \
    wget -t 5 -T 30 --waitretry=5 -q "https://github.com/sharkdp/fd/releases/download/v10.4.2/fd_10.4.2_amd64.deb" -O fd.deb && \
    sudo apt-get update && \
    sudo apt-get install -y ./ripgrep.deb ./fd.deb && \
    sudo rm -rf /tmp/* && \
    sudo apt-get clean && \
    sudo rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99proxy

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" PIP_DISABLE_PIP_VERSION_CHECK=1 && \
    pip3 install --retries 5 --timeout 120 --break-system-packages tccli awscli && \
    sudo bash -c "$(curl --retry 5 --retry-delay 3 --retry-all-errors -fsSL https://aliyuncli.alicdn.com/install.sh)"

RUN export http_proxy="${http_proxy}" https_proxy="${https_proxy}" no_proxy="${no_proxy}" \
        HTTP_PROXY="${http_proxy}" HTTPS_PROXY="${https_proxy}" NO_PROXY="${no_proxy}" \
        npm_config_fetch_retries=5 npm_config_proxy="${http_proxy}" npm_config_https_proxy="${https_proxy}" && \
    sudo npm install -g @openai/codex@0.118.0 && \
    sudo npm install -g @anthropic-ai/claude-code@2.1.98 && \
    sudo npm install -g @mariozechner/pi-coding-agent@0.73.0

RUN sudo chmod +x /usr/local/bin/kerbrute && \
    mkdir -p /home/kali/.config/nuclei && \
    printf '\n' >> /home/kali/.config/nuclei/config.yaml && \
    echo "disable-update-check: true" >> /home/kali/.config/nuclei/config.yaml && \
    echo "update-template-dir: /home/kali/.local/nuclei-templates" >> /home/kali/.config/nuclei/config.yaml && \
    sudo rm -rf /usr/lib/llvm-*/build && \
    sudo chmod o+rx /usr/lib/mysql/plugin/auth_pam_tool_dir /etc/ssl/private && \
    printf '%s\n' 'PS1="${PS1%\\n*} \[\033[0;36m\]\D{%y-%m-%d} - \t\[\033[;32m\]\n${PS1##*\\n}"' >> /home/kali/.bashrc && \
    mkdir -p /home/kali/workspace && \
    cd /home/kali/workspace && \
    git init

COPY ./.agents /home/kali/workspace/.agents
COPY ./.agents /home/kali/workspace/.claude
COPY ./AGENTS.md /home/kali/workspace/AGENTS.md
COPY ./AGENTS.md /home/kali/workspace/CLAUDE.md

WORKDIR /home/kali/workspace
