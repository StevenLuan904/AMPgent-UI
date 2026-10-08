import asyncio,re
from pathlib import Path

raise RuntimeError('deprecated blind-replace lifecycle entry; use explicit round/claim parameterization')

src=Path(__file__).with_name('r129_finish_generation_calls.py').read_text(encoding='utf-8').replace('r129','r130')
rows="""ROWS=[
 ('acea','8ba70138-f50e-5238-979a-92057735ae8e','2026-10-08T22:52:48.302127342+00:00',1655620,'d218c389db305c7b619bc0aa12a2bdacbcbd5f13c8adb9e32fc7baa0ba16560a','e4e8b441114fcb8eb579cb68c53b5d9d024211404c25c487320f626601613ecd'),
 ('vegfa','ef12d99c-60f8-55d5-bed0-db0b8952a650','2026-10-08T22:52:48.348589890+00:00',1655630,'ba0b398e02d32f0088bd8028bfe539237d24944687b17c7134bc9c239e11a190','c0e933fe32dabfc12114c2541b70dba9bae404a569beb83f2fddae1d4b2536d5')]
"""
src=re.sub(r'ROWS=\[.*?\]\n\nasync def main',rows+'\nasync def main',src,flags=re.S)
ns={}; exec(compile(src,'<r130-generation-finish>','exec'),ns)
if __name__=='__main__': asyncio.run(ns['main']())
