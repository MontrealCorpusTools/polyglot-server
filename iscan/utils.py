import os
import sys
import shutil
import subprocess
from urllib.request import urlretrieve

from django.conf import settings

import logging
log = logging.getLogger(__name__)


def is_port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def get_used_ports():
    from .models import Database
    databases = Database.objects.all()
    ports = []
    for d in databases:
        ports.extend([d.neo4j_http_port, d.neo4j_https_port, d.neo4j_bolt_port, d.neo4j_admin_port,
                      d.influxdb_http_port, d.influxdb_meta_port, d.influxdb_udp_port, d.influxdb_admin_port])
    return ports


def make_influxdb_safe(string):
    if not isinstance(string, str):
        return string
    return string.replace("\\", "/")


def download_neo4j():
    if sys.platform.startswith('win'):
        dist_string = 'windows.zip'
        path = os.path.join(settings.POLYGLOT_TEMP_DIR, 'neo4j-{}.zip'.format(settings.NEO4J_VERSION))
    else:
        dist_string = 'unix.tar.gz'
        path = os.path.join(settings.POLYGLOT_TEMP_DIR, 'neo4j-{}.tar.gz'.format(settings.NEO4J_VERSION))
    if os.path.exists(path):
        return path
    os.makedirs(settings.POLYGLOT_TEMP_DIR, exist_ok=True)

    download_link = 'https://neo4j.com/artifact.php?name=neo4j-community-{version}-{dist_string}'.format(
        version=settings.NEO4J_VERSION, dist_string=dist_string)

    archive_path, headers = urlretrieve(download_link, path, data=None)
    return archive_path


def extract_neo4j(database_name, archive_path):
    database_directory = os.path.join(settings.POLYGLOT_DATA_DIRECTORY, database_name)
    neo4j_directory = os.path.join(database_directory, 'neo4j')
    if os.path.exists(neo4j_directory):
        return False
    shutil.unpack_archive(archive_path, database_directory)
    for d in os.listdir(database_directory):
        if d.startswith('neo4j'):
            os.rename(os.path.join(database_directory, d), neo4j_directory)
    return True


def download_influxdb():
    if sys.platform.startswith('win'):
        dist_string = 'windows_amd64.zip'
        path = os.path.join(settings.POLYGLOT_TEMP_DIR, 'influxdb-{}.zip'.format(settings.INFLUXDB_VERSION))
    else:
        dist_string = 'linux_amd64.tar.gz'
        path = os.path.join(settings.POLYGLOT_TEMP_DIR, 'influxdb-{}.tar.gz'.format(settings.INFLUXDB_VERSION))
    if os.path.exists(path):
        return path
    os.makedirs(settings.POLYGLOT_TEMP_DIR, exist_ok=True)

    download_link = 'https://dl.influxdata.com/influxdb/releases/influxdb-{version}_{dist_string}'.format(
        version=settings.INFLUXDB_VERSION, dist_string=dist_string)
    archive_path, headers = urlretrieve(download_link, path, data=None)
    return archive_path


def extract_influxdb(database_name, archive_path):
    database_directory = os.path.join(settings.POLYGLOT_DATA_DIRECTORY, database_name)
    influxdb_directory = os.path.join(database_directory, 'influxdb')
    if os.path.exists(influxdb_directory):
        return False
    shutil.unpack_archive(archive_path, database_directory)
    for d in os.listdir(database_directory):
        if d.startswith('influxdb'):
            os.rename(os.path.join(database_directory, d), influxdb_directory)
    return True


def clear_cached_archives():
    shutil.rmtree(settings.POLYGLOT_TEMP_DIR, ignore_errors=True)

def get_pids():
    pids = []
    if sys.platform.startswith('win'):
        neo4j_finder = 'WMIC PROCESS get Processid,Caption,Commandline'
    else:
        neo4j_finder = 'ps S'
    proc = subprocess.Popen(neo4j_finder, shell=True,
                            stdout=subprocess.PIPE)
    stdout, stderr = proc.communicate()
    for line in stdout.decode('utf8').splitlines():
        try:
            pids.append(int(line.strip().split()[0]))
        except ValueError:
            pass
    return pids


def run_spade_script(script, target, reset=False, log=None):
    ''' Runs a SPADE script using whatever version of Python
    that is running the server as a subprocess. If log is not None,
    it will write the script output to that file'''
    cmd = [sys.executable, script, target]
    if reset:
        cmd.append("-r")
    if settings.DOCKER:
        cmd.append('-d') # Flag for running scripts in docker mode
    if log is None:
        results = subprocess.run(cmd, cwd=settings.SPADE_SCRIPT_DIRECTORY)
    else:
        with open(log, 'w') as f:
            results = subprocess.run(cmd, cwd=settings.SPADE_SCRIPT_DIRECTORY,
                    stdout=f, stderr=subprocess.STDOUT)

    if results.returncode != 0:
        raise Exception("The script did not finish successfully")
