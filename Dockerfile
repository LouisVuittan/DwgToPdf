FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        xvfb \
        libxcb-util1 \
        libxcb-xinerama0 \
        libxcb-cursor0 \
        libxcb-keysyms1 \
        libxcb-render-util0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxkbcommon-x11-0 \
        libgl1 \
        libegl1 \
        curl && \
    rm -rf /var/lib/apt/lists/*

# ODA File Converter 다운로드 & 설치
RUN curl -L -o /tmp/oda.deb \
        "https://www.opendesign.com/guestfiles/get?filename=ODAFileConverter_QT6_lnxX64_8.3dll_26.9.deb" && \
    dpkg -i /tmp/oda.deb || apt-get install -f -y && \
    rm /tmp/oda.deb && \
    # libxcb-util 심볼릭 링크 (Ubuntu 22+ 호환)
    ln -sf /usr/lib/x86_64-linux-gnu/libxcb-util.so.1 /usr/lib/x86_64-linux-gnu/libxcb-util.so.0 2>/dev/null || true

# ODA는 GUI 앱이라 headless 환경에서 xvfb로 감싸야 함
# wrapper 스크립트 생성
RUN echo '#!/bin/bash\nxvfb-run -a /usr/bin/ODAFileConverter "$@"' > /usr/local/bin/ODAFileConverter && \
    chmod +x /usr/local/bin/ODAFileConverter

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

CMD gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --timeout 120 --workers 2