"""Bounded content-addressed local archive; replace through this interface for cloud."""
import gzip
import hashlib
import os
from pathlib import Path
import re
import tempfile


class FileArchive:
    durability='LOCAL_PERSISTENT'

    def __init__(self,root,max_object_bytes=4*1024*1024,max_total_bytes=128*1024*1024,max_objects=10000):
        if os.getenv('VERCEL'):
            raise RuntimeError('local raw archive cannot run on ephemeral Vercel storage')
        self.root=Path(root).resolve()
        self.max_object_bytes=max_object_bytes;self.max_total_bytes=max_total_bytes
        self.max_objects=max_objects
        self.root.mkdir(parents=True,exist_ok=True)

    def path(self,key):
        if not re.fullmatch(r'[0-9a-f]{64}',key):raise ValueError('invalid archive hash')
        return self.root/(key+'.json.gz')

    def put(self,body):
        if not isinstance(body,bytes) or len(body)>self.max_object_bytes:raise ValueError('raw response exceeds object budget')
        key=hashlib.sha256(body).hexdigest();target=self.path(key)
        packed=gzip.compress(body,mtime=0)
        if target.exists():
            if self.read(key)!=body:raise ValueError('archive checksum mismatch')
        else:
            files=list(self.root.glob('*.json.gz'))
            if len(files)>=self.max_objects:raise OSError('local archive object budget exhausted')
            used=sum(p.stat().st_size for p in files)
            if used+len(packed)>self.max_total_bytes:raise OSError('local archive capacity exhausted')
            name=None
            try:
                with tempfile.NamedTemporaryFile(dir=self.root,prefix='upload-',delete=False) as output:
                    name=output.name;output.write(packed);output.flush();os.fsync(output.fileno())
                os.replace(name,target)
            finally:
                if name and Path(name).exists():Path(name).unlink()
        return {'sha256':key,'compressed_bytes':target.stat().st_size,'uncompressed_bytes':len(body),
                'backend':self.durability,'archive_version':'gzip-json-1'}

    def read(self,key):
        with gzip.open(self.path(key),'rb') as source:body=source.read(self.max_object_bytes+1)
        if len(body)>self.max_object_bytes or hashlib.sha256(body).hexdigest()!=key:raise ValueError('archive checksum mismatch')
        return body

    def stats(self):
        files=list(self.root.glob('*.json.gz'))
        return {'objects':len(files),'compressed_bytes':sum(p.stat().st_size for p in files),
                'backend':self.durability,'max_objects':self.max_objects,'max_total_bytes':self.max_total_bytes}
