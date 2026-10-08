import asyncio
from pathlib import Path

raise RuntimeError('deprecated blind-replace registration entry; use generation_call_registration_helper.py and a parameterized writer')

src=Path(__file__).with_name('r129_record_generation_calls.py').read_text(encoding='utf-8').replace('r129','r130')
ns={}; exec(compile(src,'<r130-generation-register>','exec'),ns)
if __name__=='__main__': asyncio.run(ns['main']())
