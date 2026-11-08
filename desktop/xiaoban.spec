# Build with scripts/build-windows.ps1. Resources contain no accounts or keys.
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

root = Path(SPECPATH).parent
build_resources = Path(os.environ.get('XIAOBAN_BUILD_RESOURCES', root / 'desktop'))
datas = [(str(root / 'web'), 'web'), (str(build_resources / 'defaults.json'), 'desktop'),
         (str(build_resources / 'update-config.json'), 'desktop')]
binaries, hidden = [], []
for package in ['webview', 'langgraph', 'langchain_core', 'langsmith', 'tzdata']:
    package_data, package_binary, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_binary
    hidden += package_hidden
for distribution in ['langgraph', 'langgraph-checkpoint', 'langgraph-prebuilt', 'langgraph-sdk', 'langchain-core', 'langsmith']:
    datas += copy_metadata(distribution)
hidden += ['uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
           'uvicorn.lifespan.on', 'desktop.runtime', 'desktop.updater', 'desktop.update_apply', 'desktop.update_ui',
           'companion.app', 'companion.store',
           'companion.agent', 'companion.policy', 'companion.integrations', 'companion.memory_adapter']
a = Analysis([str(root / 'desktop/main.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hidden, hookspath=[], runtime_hooks=[],
             excludes=['mem0', 'tkinter', 'pytest', 'IPython'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='Xiaoban-Windows', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, version=os.environ.get('XIAOBAN_BUILD_VERSION_FILE'))
