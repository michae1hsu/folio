"""Install a pinned, SHA-256 verified Gitleaks executable in ignored local tools."""
import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '8.30.1'
CHECKSUMS = {
    'darwin_arm64.tar.gz': 'b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5',
    'darwin_x64.tar.gz': 'dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709',
    'linux_arm64.tar.gz': 'e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080',
    'linux_armv6.tar.gz': '5c2a4ee657a27614e10352bed2b8f1018ef9b05fc6c037cf737776bbe1255766',
    'linux_armv7.tar.gz': '8d39f0d94ba0d774b2282187656fb039a2d82893ec1fd6be7d7121aae759a57d',
    'linux_x32.tar.gz': 'a87ba11adab22b4d6c6ea28b2da60f09154d5c2fdb44d4b07015d1e0433daecb',
    'linux_x64.tar.gz': '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb',
    'windows_arm64.zip': 'b95f5e4f5c425cedca7ee203d9afd29597e692c4924a12ed42f970537c72cc0f',
    'windows_x32.zip': '190ad53db301eec3e90afe3a1a75270768b8ebf89e731345e19421c32c1ae1a1',
    'windows_x64.zip': 'd29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e',
}


def asset_name(system=None, machine=None):
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {'amd64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64',
            'i386': 'x32', 'i686': 'x32', 'x86': 'x32', 'armv6l': 'armv6', 'armv7l': 'armv7'}.get(machine)
    name = f'{system}_{arch}.{"zip" if system == "windows" else "tar.gz"}'
    if name not in CHECKSUMS:
        raise ValueError('Unsupported platform')
    return name


def verified_executable(archive, asset):
    if hashlib.sha256(archive).hexdigest() != CHECKSUMS[asset]:
        raise ValueError('Archive checksum mismatch')
    if asset.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            return package.read('gitleaks.exe')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
        member = package.getmember('gitleaks')
        if not member.isfile():
            raise ValueError('Executable is not a regular file')
        with package.extractfile(member) as executable:
            return executable.read()


def main():
    try:
        asset = asset_name()
        url = f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{asset}'
        with urllib.request.urlopen(url, timeout=60) as response:
            archive = response.read(50 * 1024 * 1024 + 1)
        if len(archive) > 50 * 1024 * 1024:
            raise ValueError('Archive is too large')
        executable = verified_executable(archive, asset)
        target = ROOT / '.tools' / 'gitleaks' / ('gitleaks.exe' if asset.startswith('windows_') else 'gitleaks')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(executable)
        temporary.chmod(0o755)
        temporary.replace(target)
    except Exception:
        print('Gitleaks installation failed; download, checksum, or platform validation did not pass.')
        return 2
    print(f'Installed Gitleaks {VERSION}; release archive SHA-256 verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
