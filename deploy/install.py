#!/usr/bin/env python3
"""Interactive application-only Docker installation; Python standard library only."""
import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

if __package__:
    from .preflight import check_compose, check_tools
else:
    from preflight import check_compose, check_tools


ROOT = Path(__file__).resolve().parents[1]
ASSIGNMENT = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=.*$')


def env_value(text, name):
    """Read managed single-line literals, without executing a dotenv file."""
    values = re.findall(r'^\s*(?:export\s+)?' + name + r'\s*=([^\n]*)$', text, re.M)
    if not values:
        return ''
    value = values[-1].strip()
    if value.startswith("'"):
        match = re.fullmatch(r"'([^']*)'\s*(?:#.*)?", value)
        if match:
            return match[1]
    else:
        value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if not any(char in value for char in "\\$\"'\r\n"):
            return value
    raise ValueError(name + ' 请使用单行字面值；复杂引用或转义配置请按文档手动安装。')


def valid_origin(value):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and bool(parsed.hostname)
                and parsed.netloc == parsed.netloc.lower()
                and not any((parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment))
                and (parsed.port is None or 1 <= parsed.port <= 65535)
                and not any(char.isspace() or char in "\\$'\"#?" for char in value))
    except ValueError:
        return False


def valid_key(value):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value))


def prompt_value(label, current, valid, message, *, hidden=False):
    while True:
        suffix = (' [已配置，回车保留]' if hidden else ' [' + current + ']') if current else ''
        value = (getpass.getpass(label + suffix + '：') if hidden else input(label + suffix + '：')).strip()
        value = value or current
        if valid(value):
            return value
        print(message, file=sys.stderr)


def configure(root, proxy_network=None, image=None):
    path = root / '.env'
    if path.is_symlink():
        raise ValueError('.env 为符号链接，请按部署文档手动配置，安装器不会替换它。')
    existing = path.exists()
    text = (path if existing else root / '.env.example').read_text(encoding='utf-8')
    origin = env_value(text, 'PUBLIC_ORIGIN') if existing else ''
    if origin and not valid_origin(origin):
        print('现有 PUBLIC_ORIGIN 无效，请重新填写。', file=sys.stderr)
        origin = ''
    origin = prompt_value('PUBLIC_ORIGIN（完整 HTTPS 地址，可含端口）', origin, valid_origin,
                          '请输入完整 HTTPS origin，例如 https://checkin.example.com:60000，不含路径或尾部斜线。')
    key = env_value(text, 'TIANDITU_KEY')
    print('请使用自己的天地图地图服务 Key：https://lbs.tianditu.gov.cn/\n'
          'Key 将用于浏览器请求，请在平台限制站点和配额；输入不回显。')
    key = prompt_value('天地图 Key', key, valid_key, 'Key 不能为空，仅支持字母、数字、下划线和短横线。', hidden=True)
    updates = {'PUBLIC_ORIGIN': origin, 'MAP_PROVIDER': 'tianditu', 'TIANDITU_KEY': key}
    gateway = env_value(text, 'GATEWAY_SECRET')
    if gateway:
        if len(gateway) < 32 or not gateway.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in gateway):
            raise ValueError('现有 GATEWAY_SECRET 无效，请按部署文档修正；安装器不会轮换已有密钥。')
    else:
        updates['GATEWAY_SECRET'] = secrets.token_hex(32)
    if proxy_network:
        updates['PROXY_NETWORK'] = proxy_network
    if image:
        updates['BACKEND_IMAGE'] = image
    lines, written = [], set()
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        name = match[1] if match else None
        if name in updates:
            if name not in written:
                lines.append(name + '=' + updates[name])
                written.add(name)
        else:
            lines.append(line)
    lines.extend(name + '=' + value for name, value in updates.items() if name not in written)
    # Atomic replacement prevents an interrupted prompt/write from truncating .env.
    descriptor, temporary = tempfile.mkstemp(prefix='.env.install-', dir=root)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write('\n'.join(lines) + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('配置已保存到 .env（权限 0600），默认地图为天地图。')
    print('内部代理密钥已保留。' if gateway else '已生成内部代理密钥，请从本机 .env 配置到自己的反向代理。')


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description='交互配置天地图并安装 Docker 后端；不管理反向代理或证书。')
    parser.add_argument('--configure-only', action='store_true', help='仅生成/更新 .env，不调用 Docker')
    parser.add_argument('--proxy-network', metavar='NAME', help='使用已有容器代理网络（不会创建网络）')
    parser.add_argument('--image', metavar='IMAGE', help='拉取并运行指定的已发布 Linux amd64 镜像，不进行源码构建')
    args = parser.parse_args(argv)
    try:
        if args.proxy_network and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.proxy_network):
            raise ValueError('代理网络名称仅支持字母、数字、点、下划线和短横线。')
        if args.image and (len(args.image) > 256 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*', args.image)):
            raise ValueError('请填写有效的镜像引用，不得包含空格、凭据或 shell 表达式。')
        if not sys.stdin.isatty():
            raise ValueError('请在交互式终端运行安装器；CI / 自动化请直接配置 .env 并运行 Docker Compose。')
        if not args.configure_only:
            print('\n'.join(check_tools(args.proxy_network, build=not args.image)))
        if args.image:
            configure(root, args.proxy_network, image=args.image)
        else:
            configure(root, args.proxy_network)
        if args.configure_only:
            return 0
        command = ['docker', 'compose', '--env-file', str(root / '.env'), '-f', str(root / 'compose.yaml')]
        if args.proxy_network:
            command.extend(['-f', str(root / 'deploy/compose.proxy-network.yaml')])
        # Shell exports must not override settings just saved by this installer.
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('PUBLIC_ORIGIN', 'GATEWAY_SECRET', 'MAP_PROVIDER', 'TIANDITU_KEY', 'PROXY_NETWORK', 'BACKEND_IMAGE')}
        environment['COMPOSE_BAKE'] = 'false'
        operations = [['config', '--quiet']]
        if args.image:
            operations.extend([['pull', 'backend'], ['up', '-d', '--no-build', '--wait', 'backend']])
        else:
            operations.append(['up', '-d', '--build', '--wait', 'backend'])
        operations.append(['ps', 'backend'])
        for arguments in operations:
            subprocess.run(command + arguments, cwd=root, env=environment, check=True)
        print('应用已启动并通过容器健康检查；请继续按部署文档接入自己的 HTTPS 反向代理。')
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\n操作已中止；尚未完成配置时不会改写 .env，已保存的配置会保留。', file=sys.stderr)
    except UnicodeError:
        print('.env 需要使用 UTF-8 编码，请检查配置文件。', file=sys.stderr)
    except ValueError as error:
        print(str(error), file=sys.stderr)
    except OSError:
        print('无法运行命令或读写配置，请检查 Docker、文件权限和磁盘空间。', file=sys.stderr)
    except subprocess.CalledProcessError:
        print('Docker 操作失败，已保存的 .env 保留；修复后可按文档重试。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
