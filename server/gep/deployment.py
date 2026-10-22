"""First-run inputs and public, inactive domain configuration (no DNS calls)."""
import ipaddress
import re
import unicodedata

from core.researcher_passwords import password_problem


def setup_values(values):
    username = unicodedata.normalize('NFKC', str(values.get('username', '')).strip())
    password = values.get('password', '')
    if not 1 <= len(username) <= 150 or not re.fullmatch(r'[\w.@+-]+', username):
        raise ValueError('用户名为 1–150 个字符，可使用文字、数字及 @ . + - _。')
    if not isinstance(password, str) or len(password) > 4096 or password_problem(password):
        raise ValueError('密码至少 6 位，包含大写字母、小写字母、数字和符号。')
    if password != values.get('password_confirm'):
        raise ValueError('两次输入的密码不一致。')
    domain = str(values.get('domain', '')).strip().lower().rstrip('.')
    if domain:
        try:
            domain = domain.encode('idna').decode('ascii')
        except UnicodeError:
            raise ValueError('请输入有效域名，例如 example.com；不要填写网址、端口或路径。') from None
        labels = domain.split('.')
        valid = len(labels) >= 2 and len('r-' + '0' * 32 + '.experiment.' + domain) <= 253 and all(
            re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
        try:
            ipaddress.ip_address(domain)
            valid = False
        except ValueError:
            pass
        if not valid or labels[-1] in ('localhost', 'local', 'internal', 'test', 'invalid', 'example') or labels[-1].isdigit():
            raise ValueError('请输入有效域名，例如 example.com；不要填写 IP、网址、端口或路径。')
    return username, password, domain


def domain_config(domain, port):
    result = {
        'mode': 'local', 'domain': domain, 'domain_status': 'pending' if domain else 'not_configured',
        'admin_url': f'http://admin.localhost:{port}',
        'participant_url': f'http://experiment.localhost:{port}',
    }
    if domain:
        result['public_admin_url'] = f'https://admin.{domain}'
        result['public_participant_url'] = f'https://experiment.{domain}'
    return result


def domain_files(domain):
    """Candidate files only; nothing reads or activates them automatically."""
    return {
        'Caddyfile.pending': (
            '# Pending: verify DNS, network access and deployment before enabling.\n'
            '# DNS must also resolve *.experiment.<domain>; no DNS is changed here.\n'
            '{\n    on_demand_tls {\n        ask http://gep:8000/_internal/tls-allow\n    }\n}\n\n'
            f'admin.{domain}, experiment.{domain} {{\n'
            '    @internal path /_internal/*\n    respond @internal 404\n'
            '    reverse_proxy gep:8000 {\n        header_up X-Forwarded-Proto https\n    }\n}\n\n'
            'https:// {\n    tls {\n        on_demand\n    }\n'
            '    @internal path /_internal/*\n    respond @internal 404\n'
            '    reverse_proxy gep:8000 {\n        header_up X-Forwarded-Proto https\n    }\n}\n'),
        'domain.env.pending': (
            '# Pending: enable only together with a verified HTTPS reverse proxy.\n'
            f'GEP_ADMIN_HOST=admin.{domain}\nGEP_EXPERIMENT_HOST=experiment.{domain}\n'
            f'GEP_ADMIN_ORIGIN=https://admin.{domain}\nGEP_PUBLIC_API=https://experiment.{domain}\nGEP_HTTPS=1\nGEP_TRUST_PROXY=1\n'),
    }
