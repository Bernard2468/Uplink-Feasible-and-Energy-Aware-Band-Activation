#!/usr/bin/env python3
"""Privacy-minimal local machine readiness report; no host/user identifiers."""
import argparse
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description='Print non-identifying project prerequisites')
    parser.add_argument('--save', action='store_true', help='write results/environment_check.json')
    args=parser.parse_args()
    packages={}
    for name in ('numpy','scipy','sionna-rt','mitsuba','drjit','cppyy','pybind11','highspy','ortools'):
        try:
            packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name]=None
    total_mem_gb=None
    if Path('/proc/meminfo').exists():
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
                total_mem_gb=round(int(line.split()[1])/1024**2,1)
                break
    usage=shutil.disk_usage(Path.cwd())
    output={'purpose':'LOCAL SOFTWARE READINESS, NOT RADIO RESULTS',
            'os':platform.system(),'os_release':platform.release(),
            'architecture':platform.machine(),'python':sys.version.split()[0],
            'cpu_threads_os':os.cpu_count(), 'ram_total_gb':total_mem_gb,
            'disk_free_gb_project_fs':round(usage.free/1024**3,1),
            'is_wsl': 'microsoft' in platform.release().lower(),
            'executables':{x: bool(shutil.which(x)) for x in ('git','g++','cmake','ninja','llvm-config','nvidia-smi')},
            'python_packages':packages}
    if shutil.which('nvidia-smi'):
        try:
            gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader'],text=True,timeout=8)
            output['gpu']=gpu.strip().splitlines()
        except (subprocess.SubprocessError,OSError) as err:
            output['gpu_query_error']=type(err).__name__
    print(json.dumps(output,indent=2))
    if args.save:
        out=Path(__file__).resolve().parents[1]/'results'/'environment_check.json'
        out.parent.mkdir(exist_ok=True,parents=True)
        out.write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
        print(f'Saved {out}')
    print('No hostname, user account, IP address or location has been collected.')

if __name__=='__main__':
    main()
