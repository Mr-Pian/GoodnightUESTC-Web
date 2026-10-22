#!/usr/bin/env python3
"""Bounded, local Docker capability checks; never pull images or download packages."""
import argparse
import json
import os
import re
import subprocess
import sys


def run(arguments, message):
    try:
        return subprocess.run(arguments, check=True, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Docker diagnostics can include private context endpoints and proxy URLs.
        raise ValueError(message) from None


def version(value):
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('无法识别工具版本，请使用官方稳定版 Docker / Compose / buildx。')
    return tuple(map(int, match.groups()))


def check_compose(proxy_network=False):
    value = run(['docker', 'compose', 'version', '--short'],
                '无法运行 Compose v2；请安装 docker-compose-plugin。旧 docker-compose v1 不受支持。')
    minimum = (2, 24, 4) if proxy_network else (2, 20, 0)
    if version(value) < minimum:
        raise ValueError('Compose 至少需要 ' + '.'.join(map(str, minimum)) + '，请更新 Compose 插件。')
    return value


def check_tools(proxy_network=False, *, build=True):
    raw = run(['docker', 'version', '--format', '{{json .}}'],
              '无法连接 Docker Engine。请确认 Docker 已启动、当前 Docker context 正确，'
              '且当前用户有访问 daemon 的权限；不要将 socket 改成全员可写。')
    try:
        details = json.loads(raw)
        client = details['Client']['Version']
        server = details['Server']['Version']
        operating_system = details['Server']['Os']
        architecture = details['Server']['Arch']
    except (ValueError, KeyError, TypeError):
        raise ValueError('Docker 未返回完整客户端和服务端版本，请检查 daemon 连接与权限。') from None
    if version(client) < (24, 0, 0) or version(server) < (24, 0, 0):
        raise ValueError('本项目要求 Docker CLI 与 Engine 均 >= 24.0.0，请更新后再安装。')
    if operating_system != 'linux':
        raise ValueError('需要 Linux 容器环境；Docker Desktop 请切换到 Linux containers。')
    compose = check_compose(proxy_network)
    messages = ['Docker CLI / Engine >= 24: PASS', 'Compose ' + compose + ': PASS']
    if build:
        if os.environ.get('DOCKER_BUILDKIT') == '0':
            raise ValueError('DOCKER_BUILDKIT=0 禁用了现代构建器，请 unset DOCKER_BUILDKIT 后重试。')
        buildx = run(['docker', 'buildx', 'version'],
                     '源码构建需要 buildx >= 0.12.0，请安装 docker-buildx-plugin；'
                     '不构建源码可使用安装器 --image 指定已发布镜像。')
        if version(buildx) < (0, 12, 0):
            raise ValueError('buildx 至少需要 0.12.0，请更新 docker-buildx-plugin。')
        run(['docker', 'buildx', 'inspect'],
            'buildx 无法访问当前 builder，请检查 docker buildx ls 和 Docker context；'
            '预检未启动构建或下载依赖。')
        messages.append('buildx >= 0.12 / current builder: PASS')
    else:
        if architecture not in ('amd64', 'x86_64'):
            raise ValueError('当前发行镜像仅支持 Linux amd64；其他架构请自行构建并验证。')
        messages.append('Linux amd64 image runtime: PASS (buildx not required)')
    return messages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-only', action='store_true', help='仅检查预构建镜像运行要求，不检查 buildx')
    parser.add_argument('--proxy-network', action='store_true', help='检查共享网络所需 Compose 版本')
    args = parser.parse_args(argv)
    try:
        print('\n'.join(check_tools(args.proxy_network, build=not args.image_only)))
        print('本地工具检查通过；未下载镜像或依赖，不代表构建网络、地图或学校服务已验证。')
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
