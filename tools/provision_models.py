"""Provision models while internet is available. Never run this on a production runtime."""
import os,argparse
from smriti_voice.config import Settings,LanguageRegistry
from smriti_voice.asr import ProviderFactory

def main():
 p=argparse.ArgumentParser(); p.add_argument('--language',required=True); a=p.parse_args(); os.environ['SMRITI_PROVISIONING']='1'
 s=Settings.load(); pack=LanguageRegistry(s.language_pack_dir).get(a.language); f=ProviderFactory(s.language_pack_dir.parent)
 if not pack.providers: raise SystemExit('No provider configured')
 last=None
 for provider in pack.providers:
  try:
   print('Provisioning',a.language,provider,pack.model_id); f.create(provider,a.language,pack.model_id).warmup(); print('READY'); return
  except Exception as e: last=e; print('FAILED',type(e).__name__,e)
 raise SystemExit(f'No provider provisioned: {last}')
if __name__=='__main__':main()
