# 基础镜像可通过 --build-arg PYTHON_IMAGE=<其他源> 切换,方便国内拉取加速。
# 常用国内源(任选其一):
#   docker.m.daocloud.io/library/python:3.12-slim
#   registry.cn-hangzhou.aliyuncs.com/library/python:3.12-slim
#   ccr.ccs.tencentyun.com/library/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# apt 源也可以换国内镜像,默认空走 deb.debian.org(国内常超时)。
# 常用值:
#   mirrors.aliyun.com   mirrors.tuna.tsinghua.edu.cn   mirrors.ustc.edu.cn
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
            /etc/apt/sources.list 2>/dev/null || true; \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
            /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ /tmp/requirements/
RUN pip install -r /tmp/requirements/prod.txt

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 6549

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6549"]
