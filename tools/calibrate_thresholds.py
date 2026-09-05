"""Offline threshold calibration helper. Expects CSV with columns: text,label.
Run on native-speaker validation data; never calibrate on the test set.
"""
import argparse,csv
from pathlib import Path
from smriti_voice.intents import IntentRouter
from smriti_voice.config import Settings,LanguageRegistry

def main():
 p=argparse.ArgumentParser(); p.add_argument('--language',required=True); p.add_argument('--csv',required=True); a=p.parse_args()
 s=Settings.load(); pack=LanguageRegistry(s.language_pack_dir).get(a.language); r=IntentRouter(pack.commands_path,0,0)
 rows=list(csv.DictReader(open(a.csv,encoding='utf-8'))); print('samples',len(rows))
 best=(0,0)
 for th in range(50,96):
  ok=0
  for x in rows:
   m=r.classify(x['text']); pred=m.intent if m.accepted else 'UNKNOWN'; ok += pred==x['label']
  acc=ok/max(1,len(rows))
  if acc>best[1]:best=(th,acc)
 print('candidate_threshold,accuracy',best)
if __name__=='__main__':main()
