"""Local EIS Taxonomy Manager: stdlib web/API + SQLite + optional openpyxl."""
from __future__ import annotations
import csv, io, json, os, re, sqlite3, threading, uuid, traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote
from db import connect as open_database, has_postgres_url

ROOT=Path(__file__).parent
DB=Path(os.environ.get('EIS_DB', ROOT/'data'/'eis.db'))
LOCK=threading.RLock()
DEMO=[
 {'SKU':'SKF-DEMO-001','Description':'SKF deep groove ball bearing 6205','MPN':'6205-2RS','Product Series':'6200','Product Type Code':'BRG','Active':'Yes','Sales':1250},
 {'SKU':'SKF-DEMO-002','Description':'SKF deep groove ball bearing 6206','MPN':'6206-2RS','Product Series':'6200','Product Type Code':'BRG','Active':'Yes','Sales':980},
 {'SKU':'SKF-DEMO-003','Description':'SKF tapered roller bearing 30205','MPN':'30205-J2','Product Series':'30000','Product Type Code':'BRG','Active':'Yes','Sales':440},
 {'SKU':'DEMO-004','Description':'unknown maintenance kit','MPN':'KIT-01','Product Series':'','Product Type Code':'KIT','Active':'Yes','Sales':20},
 {'SKU':'DEMO-005','Description':'deep groove bearing, alternate marker','MPN':'ALT-05','Product Series':'6200','Product Type Code':'BRG','Active':'No','Sales':0},
]
@contextmanager
def connect():
 c=open_database(DB)
 try:
  yield c; c.commit()
 except Exception:
  c.rollback(); raise
 finally:c.close()
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def level_tuple(row): return tuple(row[f'l{i}'] or '' for i in range(1,5))
def init():
 with connect() as c:
  schema='''CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,name TEXT,uploaded TEXT,rows INTEGER,sku_col TEXT,description_col TEXT,active_col TEXT,mpn_col TEXT,series_col TEXT,type_col TEXT,sales_col TEXT);
  CREATE TABLE IF NOT EXISTS rows(id INTEGER PRIMARY KEY AUTOINCREMENT,file_id TEXT,source_row INTEGER,sku TEXT,description TEXT,mpn TEXT,series TEXT,type_code TEXT,active INTEGER,sales REAL,data TEXT,l1 TEXT,l2 TEXT,l3 TEXT,l4 TEXT,status TEXT DEFAULT 'unclassified',rule_id TEXT,trace TEXT,reviewed INTEGER DEFAULT 0,correct INTEGER,decision_by TEXT,decision_note TEXT,FOREIGN KEY(file_id) REFERENCES files(id));
  CREATE INDEX IF NOT EXISTS ix_rows_file ON rows(file_id); CREATE INDEX IF NOT EXISTS ix_rows_sku ON rows(sku);
  CREATE TABLE IF NOT EXISTS taxonomy(id TEXT PRIMARY KEY,l1 TEXT,l2 TEXT,l3 TEXT,l4 TEXT,definition TEXT,approved INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,name TEXT,field TEXT,operator TEXT,pattern TEXT,l1 TEXT,l2 TEXT,l3 TEXT,l4 TEXT,priority INTEGER DEFAULT 100,enabled INTEGER DEFAULT 1,approved INTEGER DEFAULT 0,created TEXT);
  CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT,actor TEXT,action TEXT,entity TEXT,entity_id TEXT,details TEXT);
  CREATE TABLE IF NOT EXISTS spotchecks(id INTEGER PRIMARY KEY,file_id TEXT,row_id INTEGER,expected TEXT,actual TEXT,correct INTEGER,reviewer TEXT,at TEXT);'''
  if has_postgres_url():
   schema=schema.replace('INTEGER PRIMARY KEY AUTOINCREMENT','BIGSERIAL PRIMARY KEY').replace('id INTEGER PRIMARY KEY','id BIGSERIAL PRIMARY KEY')
  c.executescript(schema)
def audit(c,action,entity,eid,details,actor='Local SME'):
 c.execute('INSERT INTO audit(at,actor,action,entity,entity_id,details) VALUES(?,?,?,?,?,?)',(now(),actor,action,entity,str(eid),json.dumps(details,ensure_ascii=False)))
def data_state():
 with connect() as c:
  files=[dict(r) for r in c.execute('SELECT * FROM files ORDER BY uploaded DESC')]
  tax=[dict(r) for r in c.execute('SELECT * FROM taxonomy ORDER BY l1,l2,l3,l4')]
  rules=[dict(r) for r in c.execute('SELECT * FROM rules ORDER BY priority,name')]
  taxonomy_paths={tuple(t[f'l{i}'] or '' for i in range(1,5)) for t in tax}
  approved_paths={tuple(t[f'l{i}'] or '' for i in range(1,5)) for t in tax if t['approved']}
  for rule in rules:
   rp=tuple(rule[f'l{i}'] or '' for i in range(1,5))
   rule['approvalBlock']=None if rule['approved'] or rp in approved_paths else ('Approve the matching taxonomy path first.' if rp in taxonomy_paths else 'Add and approve this exact path in Taxonomy nodes first.')
  counts={r['status']:r['n'] for r in c.execute('SELECT status,count(*) n FROM rows WHERE active=1 GROUP BY status')}
  n=c.execute('SELECT count(*) FROM rows').fetchone()[0]; active=c.execute('SELECT count(*) FROM rows WHERE active=1').fetchone()[0]
  ruled=c.execute("SELECT count(*) FROM rows WHERE active=1 AND status IN ('classified','approved') AND rule_id IS NOT NULL").fetchone()[0]
  manual=c.execute("SELECT count(*) FROM rows WHERE active=1 AND status IN ('classified','approved') AND rule_id IS NULL").fetchone()[0]
  checked=c.execute('SELECT count(*) FROM spotchecks').fetchone()[0]; good=c.execute('SELECT count(*) FROM spotchecks WHERE correct=1').fetchone()[0]
  suggestions,recommendations=discover(c)
  per_file=[]
  for f in files:
   fc=c.execute('SELECT count(*) active_n,sum(CASE WHEN status IN (\'classified\',\'approved\') AND rule_id IS NOT NULL THEN 1 ELSE 0 END) ruled_n FROM rows WHERE file_id=? AND active=1',(f['id'],)).fetchone()
   sc=c.execute('SELECT count(*) n,sum(correct) good FROM spotchecks WHERE file_id=?',(f['id'],)).fetchone()
   per_file.append({'fileId':f['id'],'name':f['name'],'activeRows':fc['active_n'] or 0,'ruleCoverage':round(100*(fc['ruled_n'] or 0)/(fc['active_n'] or 1),1),'spotChecks':sc['n'],'spotCheckAccuracy':round(100*(sc['good'] or 0)/(sc['n'] or 1),1) if sc['n'] else None})
  return {'files':files,'taxonomy':tax,'rules':rules,'counts':counts,'metrics':{'rows':n,'activeRows':active,'ruleCoverage':round(100*ruled/active,1) if active else 0,'classifiedByRule':ruled,'classifiedManually':manual,'unclassified':counts.get('unclassified',0),'exceptions':counts.get('exception',0),'spotChecks':checked,'spotCheckAccuracy':round(100*good/checked,1) if checked else None,'coverageTarget':90,'accuracyTarget':98},'qualityByFile':per_file,'suggestions':suggestions,'recommendations':recommendations}
def discover(c):
 # Local nearest-example and marker suggestions; recommendations are never auto-applied.
 out=[]; recommendations=[]
 for f in c.execute('SELECT id,name FROM files'):
  rows=list(c.execute("SELECT series,type_code,description,count(*) n FROM rows WHERE file_id=? AND active=1 AND status='unclassified' GROUP BY series,type_code,description ORDER BY n DESC LIMIT 100",(f['id'],)))
  tallies={}
  for r in rows:
   candidates=[]
   if r['series']: candidates.append(('series',r['series'],r['n']))
   if r['type_code']: candidates.append(('type_code',r['type_code'],r['n']))
   for field,pat,n in candidates:
    key=(field,pat); tallies[key]=tallies.get(key,0)+n
  for (field,pat),n in sorted(tallies.items(),key=lambda x:-x[1])[:8]:
   if n>=2 and not c.execute('SELECT 1 FROM rules WHERE field=? AND pattern=? AND enabled=1',(field,pat)).fetchone():
    out.append({'file':f['name'],'fileId':f['id'],'field':field,'pattern':pat,'count':n,'reason':'Repeated marker among active unclassified SKUs; inspect and map to an approved path.'})
  exemplars=list(c.execute("SELECT sku,description,l1,l2,l3,l4 FROM rows WHERE file_id=? AND active=1 AND status IN ('classified','approved') ORDER BY source_row LIMIT 3000",(f['id'],)))
  tokens=lambda text:set(re.findall(r'[a-z0-9]{3,}',(text or '').lower()))
  for target in c.execute("SELECT id,sku,description,mpn FROM rows WHERE file_id=? AND active=1 AND status IN ('exception','unclassified') ORDER BY source_row LIMIT 100",(f['id'],)):
   tt=tokens(target['description']); best=None
   if not tt:continue
   for ex in exemplars:
    et=tokens(ex['description']); union=tt|et; score=len(tt&et)/len(union) if union else 0
    if best is None or score>best[0]:best=(score,ex)
   if best and best[0]>=0.2:
    score,ex=best; recommendations.append({'file':f['name'],'fileId':f['id'],'rowId':target['id'],'sku':target['sku'],'mpn':target['mpn'],'description':target['description'],'l1':ex['l1'],'l2':ex['l2'],'l3':ex['l3'],'l4':ex['l4'],'similarSku':ex['sku'],'score':round(score*100),'reason':'Description token overlap with an already classified example in the same file; SME must verify.'})
 return out[:30],recommendations[:100]
def read_upload(name,body):
 if name.lower().endswith('.csv'):
  for enc in ('utf-8-sig','cp1252','latin-1'):
   try: return list(csv.DictReader(io.StringIO(body.decode(enc)))), 'CSV'
   except UnicodeDecodeError: continue
  raise ValueError('Could not decode CSV')
 if name.lower().endswith(('.xlsx','.xlsm')):
  try: import openpyxl
  except ImportError: raise ValueError('Excel support requires openpyxl. Install the requirements file.')
  wb=openpyxl.load_workbook(io.BytesIO(body),read_only=True,data_only=True); ws=wb.active; vals=ws.iter_rows(values_only=True); headers=[str(x or '').strip() for x in next(vals)]; return [dict(zip(headers,r)) for r in vals if any(x is not None for x in r)],'Excel'
 raise ValueError('Upload a .csv or .xlsx file')
def norm(x): return re.sub(r'[^a-z0-9]','',str(x or '').lower())
ALIASES={'sku':['sku','item','itemnumber','itemno','productid','productkey','material','partnumber','partno'],'description':['description','productdescription','shortdescription','itemdescription','productname'],'mpn':['mpn','manufacturerpartnumber','mfrpartnumber'],'series':['productseries','series','model'],'type_code':['producttypecode','typecode','producttype','class'],'active':['active','activebit','isactive','status','lifecycle'],'sales':['sales','activesales','annualsales','revenue','salesvalue']}
def pick(headers,key,requested=None):
 if requested and requested in headers:return requested
 lookup={norm(h):h for h in headers}
 for alias in ALIASES[key]:
  if norm(alias) in lookup:return lookup[norm(alias)]
 return None
def import_columns(headers, spec):
 lookup={norm(h):h for h in headers}; found={}
 for key,aliases in spec.items():
  found[key]=next((lookup[norm(a)] for a in aliases if norm(a) in lookup),None)
 return found
def import_taxonomy_records(records):
 if not records:raise ValueError('The taxonomy file has no data rows.')
 headers=list(records[0]); cols=import_columns(headers,{'l1':['Level 1','L1'],'l2':['Level 2','L2'],'l3':['Level 3','L3'],'l4':['Level 4','L4'],'definition':['Definition','Node Definition']})
 if not cols['l1'] or not cols['definition']:raise ValueError('Taxonomy upload needs Level 1 and Definition columns. Use the downloadable template.')
 paths=[]
 for i,row in enumerate(records,2):
  levels=[str(row.get(cols[f'l{n}']) or '').strip() if cols[f'l{n}'] else '' for n in range(1,5)]
  definition=str(row.get(cols['definition']) or '').strip()
  if not levels[0] or not definition:raise ValueError(f'Row {i}: Level 1 and Definition are required.')
  if any(not levels[n] and any(levels[j] for j in range(n+1,4)) for n in range(4)):raise ValueError(f'Row {i}: taxonomy levels must be contiguous.')
  paths.append((levels,definition,i))
 seen=set()
 with connect() as c:
  existing={level_tuple(r) for r in c.execute('SELECT l1,l2,l3,l4 FROM taxonomy')}
  for levels,_,i in paths:
   t=tuple(levels)
   if t in existing or t in seen:raise ValueError(f'Row {i}: taxonomy path is duplicated in this file or already exists.')
   seen.add(t)
  for levels,definition,_ in paths:c.execute('INSERT INTO taxonomy VALUES(?,?,?,?,?,?,0)',(uuid.uuid4().hex[:12],*levels,definition))
  audit(c,'import','taxonomy','batch',{'rows':len(paths),'approved':False})
 return len(paths)
def import_rule_records(records):
 if not records:raise ValueError('The rules file has no data rows.')
 headers=list(records[0]); cols=import_columns(headers,{'name':['Rule Name','Name'],'field':['Marker Field','Field'],'operator':['Operator','Match'],'pattern':['Marker','Pattern'],'priority':['Priority'],'l1':['Level 1','L1'],'l2':['Level 2','L2'],'l3':['Level 3','L3'],'l4':['Level 4','L4']})
 missing=[k for k in ('name','field','operator','pattern','l1') if not cols[k]]
 if missing:raise ValueError('Rules upload needs Rule Name, Marker Field, Operator, Pattern, and Level 1 columns. Use the downloadable template.')
 field_values={'description':'description','productdescription':'description','sku':'sku','skuid':'sku','skuidentifier':'sku','productid':'sku','itemnumber':'sku','mpn':'mpn','series':'series','productseries':'series','typecode':'type_code','producttypecode':'type_code'}
 compiled=[]
 for i,row in enumerate(records,2):
  name=str(row.get(cols['name']) or '').strip(); raw_field=norm(row.get(cols['field'])); field=field_values.get(raw_field)
  op=norm(row.get(cols['operator'])); op={'startswith':'prefix','matches':'exact','regex':'regex'}.get(op,op)
  pattern=str(row.get(cols['pattern']) or '').strip(); levels=[str(row.get(cols[f'l{n}']) or '').strip() if cols[f'l{n}'] else '' for n in range(1,5)]
  try:priority=int(row.get(cols['priority']) or 100) if cols['priority'] else 100
  except:raise ValueError(f'Row {i}: Priority must be a whole number.')
  if not name or not field or op not in ('contains','prefix','exact','regex') or not pattern:raise ValueError(f'Row {i}: provide a valid name, marker field, operator, and pattern.')
  if not levels[0] or any(not levels[n] and any(levels[j] for j in range(n+1,4)) for n in range(4)):raise ValueError(f'Row {i}: provide a contiguous taxonomy path beginning with Level 1.')
  if op=='regex':
   try:re.compile(pattern)
   except re.error as e:raise ValueError(f'Row {i}: invalid regular expression: {e}')
  compiled.append((name,field,op,pattern,*levels,priority))
 with connect() as c:
  for item in compiled:
   rid=uuid.uuid4().hex[:12]; c.execute('INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?,?,1,0,?)',(rid,*item,now()))
  audit(c,'import','rule','batch',{'rows':len(compiled),'approved':False})
 return len(compiled)
def classify_row(row,rules):
 fields={'sku':row['sku'],'description':row['description'],'mpn':row['mpn'],'series':row['series'],'type_code':row['type_code'],'data':json.loads(row['data'])}
 matches=[]
 for rule in rules:
  if not rule['enabled'] or not rule['approved']:continue
  val=fields.get(rule['field'],''); pat=rule['pattern']; op=rule['operator']
  try:
   ok=(op=='contains' and pat.lower() in str(val).lower()) or (op=='prefix' and str(val).lower().startswith(pat.lower())) or (op=='exact' and str(val).lower()==pat.lower()) or (op=='regex' and bool(re.search(pat,str(val),re.I)))
  except re.error: ok=False
  if ok:matches.append(rule)
 if not matches:return None,'No approved rule matched'
 matches.sort(key=lambda r:(int(r['priority']),r['name']))
 winners=[r for r in matches if int(r['priority'])==int(matches[0]['priority'])]
 paths={(x['l1'],x['l2'],x['l3'],x['l4']) for x in winners}
 if len(paths)>1:return None,'Conflicting top-priority rules: '+', '.join(x['name'] for x in winners)
 return winners[0],None
class Handler(BaseHTTPRequestHandler):
 server_version='EIS-Taxonomy/1.0'
 def log_message(self,*a): pass
 def send_json(self,obj,status=200):
  b=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def body(self): return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
 def do_GET(self):
  try:return self._handle_get()
  except Exception as e:
   traceback.print_exc()
   try:return self.send_json({'error':f'{type(e).__name__}: {e}'},500)
   except Exception:pass
 def _handle_get(self):
  path=urlparse(self.path).path
  if path=='/api/export':return serve_export(self)
  if path=='/api/template':
   q=__import__('urllib.parse').parse.parse_qs(urlparse(self.path).query); kind=q.get('type',[''])[0]
   headers={'taxonomy':['Level 1','Level 2','Level 3','Level 4','Definition'],'rules':['Rule Name','Marker Field','Operator','Pattern','Priority','Level 1','Level 2','Level 3','Level 4']}.get(kind)
   if not headers:return self.send_json({'error':'Template type must be taxonomy or rules.'},400)
   out=io.StringIO(newline='');csv.writer(out).writerow(headers); b=out.getvalue().encode('utf-8-sig')
   self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8');self.send_header('Content-Disposition',f'attachment; filename="eis-{kind}-template.csv"');self.send_header('Content-Length',str(len(b)));self.end_headers();return self.wfile.write(b)
  if path=='/api/state':return self.send_json(data_state())
  if path=='/api/exceptions':
   with connect() as c: rows=[dict(r) for r in c.execute("SELECT rows.*,files.name file_name FROM rows JOIN files ON files.id=rows.file_id WHERE rows.active=1 AND rows.status IN ('exception','unclassified') ORDER BY files.name,rows.source_row LIMIT 3000")]
   with connect() as c: _,recommendations=discover(c)
   return self.send_json({'rows':rows,'recommendations':recommendations})
  if path=='/api/audit':
   with connect() as c: rows=[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 500')]
   return self.send_json({'rows':rows})
  if path=='/api/rows':
   q=__import__('urllib.parse').parse.parse_qs(urlparse(self.path).query); fid=q.get('fileId',[''])[0]
   with connect() as c: rows=[dict(r) for r in c.execute('SELECT * FROM rows WHERE file_id=? ORDER BY source_row LIMIT 2000',(fid,))]
   return self.send_json({'rows':rows})
  if path=='/api/sample':
   q=__import__('urllib.parse').parse.parse_qs(urlparse(self.path).query); fid=q.get('fileId',[''])[0]
   with connect() as c: rows=[dict(r) for r in c.execute("SELECT * FROM rows WHERE file_id=? AND active=1 AND status IN ('classified','approved') ORDER BY random() LIMIT 200",(fid,))]
   return self.send_json({'rows':rows})
  if path=='/api/profile':
   q=__import__('urllib.parse').parse.parse_qs(urlparse(self.path).query); fid=q.get('fileId',[''])[0]; groups={}
   with connect() as c:
    for r in c.execute('SELECT description,mpn,series,type_code,active,sales FROM rows WHERE file_id=?',(fid,)):
     active='Active' if r['active'] else 'Inactive'
     markers=[('Product Series',r['series']),('Product Type Code',r['type_code']),('MPN prefix',str(r['mpn'] or '')[:3])]
     markers.extend(('Description stem',w) for w in set(re.findall(r'[a-z0-9]{5,}',(r['description'] or '').lower())))
     for kind,value in markers:
      if not value:continue
      key=(kind,str(value),active); item=groups.setdefault(key,{'count':0,'sales':0.0}); item['count']+=1; item['sales']+=r['sales'] or 0
   out=[{'marker':f'{k[0]} · {k[1]} · {k[2]}',**v} for k,v in sorted(groups.items(),key=lambda kv:-kv[1]['count'])[:200]]
   return self.send_json({'groups':out})
  if path=='/':
   p=ROOT/'index.html'; b=p.read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); return self.wfile.write(b)
  return self.send_json({'error':'Not found'},404)
 def do_POST(self):
  path=urlparse(self.path).path
  try:
   if path=='/api/upload':
    length=int(self.headers.get('Content-Length','0')); name=unquote(self.headers.get('X-Filename','upload.csv')); records,kind=read_upload(name,self.rfile.read(length));
    if not records: return self.send_json({'error':'The file has no data rows'},400)
    headers=list(records[0]); sku=pick(headers,'sku'); desc=pick(headers,'description')
    if not sku or not desc:return self.send_json({'error':'Could not identify SKU identifier and description columns. Rename columns to SKU and Description.'},400)
    cols={k:pick(headers,k) for k in ['active','mpn','series','type_code','sales']}; fid=uuid.uuid4().hex[:12]; valid=len(records)
    with connect() as c:
     c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)',(fid,name,now(),valid,sku,desc,cols['active'],cols['mpn'],cols['series'],cols['type_code'],cols['sales']))
     for i,r in enumerate(records,2):
      active=str(r.get(cols['active']) if cols['active'] else 'yes').strip().lower() not in ('no','n','false','0','inactive','discontinued')
      try:sales=float(str(r.get(cols['sales']) or 0).replace(',','').replace('$',''))
      except:sales=0
      fields=[str(r.get(sku) or ''),str(r.get(desc) or ''),str(r.get(cols['mpn']) or '') if cols['mpn'] else '',str(r.get(cols['series']) or '') if cols['series'] else '',str(r.get(cols['type_code']) or '') if cols['type_code'] else '']
      c.execute('INSERT INTO rows(file_id,source_row,sku,description,mpn,series,type_code,active,sales,data) VALUES(?,?,?,?,?,?,?,?,?,?)',(fid,i,*fields,int(active),sales,json.dumps(r,default=str,ensure_ascii=False)))
     audit(c,'upload','file',fid,{'name':name,'rows':valid,'headers':headers,'detected':{'sku':sku,'description':desc,**cols}})
    return self.send_json({'fileId':fid,'rows':valid,'type':kind})
   if path in ('/api/taxonomy/import','/api/rules/import'):
    length=int(self.headers.get('Content-Length','0')); name=unquote(self.headers.get('X-Filename','import.csv')); records,kind=read_upload(name,self.rfile.read(length))
    added=import_taxonomy_records(records) if path.endswith('taxonomy/import') else import_rule_records(records)
    return self.send_json({'imported':added,'type':kind})
   data=self.body()
   if path=='/api/taxonomy':
    levels=[str(data.get(f'l{i}','')).strip() for i in range(1,5)]; definition=str(data.get('definition','')).strip()
    if not levels[0] or not definition:return self.send_json({'error':'Client Level 1 and a one-line node definition are required.'},400)
    if any(not levels[i] and any(levels[j] for j in range(i+1,4)) for i in range(4)):return self.send_json({'error':'Taxonomy levels must be contiguous.'},400)
    with connect() as c:
     if c.execute('SELECT 1 FROM taxonomy WHERE l1=? AND l2=? AND l3=? AND l4=?',levels).fetchone():return self.send_json({'error':'That full path already exists.'},400)
     tid=uuid.uuid4().hex[:12]; c.execute('INSERT INTO taxonomy VALUES(?,?,?,?,?,?,?)',(tid,*levels,definition,0)); audit(c,'create','taxonomy',tid,{'path':levels,'definition':definition})
    return self.send_json({'id':tid})
   if path=='/api/taxonomy/approve':
    with connect() as c: c.execute('UPDATE taxonomy SET approved=1'); audit(c,'approve','taxonomy','all',{'count':c.total_changes})
    return self.send_json({'ok':True})
   if path=='/api/rules':
    name=str(data.get('name','')).strip(); field=data.get('field'); op=data.get('operator'); pat=str(data.get('pattern','')).strip(); levels=[str(data.get(f'l{i}','')).strip() for i in range(1,5)]
    if not name or not pat or field not in ('description','sku','mpn','series','type_code') or op not in ('contains','prefix','exact','regex'):return self.send_json({'error':'Provide a name, supported match field/operator, and pattern.'},400)
    if not levels[0] or any(not levels[i] and any(levels[j] for j in range(i+1,4)) for i in range(4)):return self.send_json({'error':'Enter a contiguous full path under Client Level 1.'},400)
    with connect() as c:
     rid=uuid.uuid4().hex[:12]; c.execute('INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?,?,1,0,?)',(rid,name,field,op,pat,*levels,int(data.get('priority',100)),now())); audit(c,'create','rule',rid,data)
    return self.send_json({'id':rid})
   if path=='/api/rules/approve':
    rid=data.get('id');
    with connect() as c:
     candidates=[dict(x) for x in c.execute('SELECT * FROM rules WHERE approved=0'+(' AND id=?' if rid else ''),(rid,) if rid else ())]
     approved_paths={level_tuple(x) for x in c.execute('SELECT l1,l2,l3,l4 FROM taxonomy WHERE approved=1')}
     eligible=[r for r in candidates if (r['l1'],r['l2'],r['l3'],r['l4']) in approved_paths]
     for rule in eligible:c.execute('UPDATE rules SET approved=1 WHERE id=?',(rule['id'],))
     skipped=len(candidates)-len(eligible); audit(c,'approve','rule',rid or 'all',{'approved':len(eligible),'stillPending':skipped})
    return self.send_json({'ok':True,'approved':len(eligible),'stillPending':skipped})
   if path=='/api/classify':
    fid=data.get('fileId');
    with connect() as c:
     approved_paths={level_tuple(x) for x in c.execute('SELECT l1,l2,l3,l4 FROM taxonomy WHERE approved=1')}
     rules=[dict(x) for x in c.execute('SELECT * FROM rules WHERE approved=1 AND enabled=1 ORDER BY priority,name') if level_tuple(x) in approved_paths]
     if not rules:return self.send_json({'error':'No approved active rules. Approve taxonomy and rules before bulk assignment.'},400)
     query='SELECT * FROM rows WHERE active=1'+(' AND file_id=?' if fid else '')
     rows=c.execute(query,(fid,) if fid else ()).fetchall(); nclass=nexc=0
     for r in rows:
      rule,err=classify_row(r,rules)
      if err:c.execute("UPDATE rows SET status='exception',l1=NULL,l2=NULL,l3=NULL,l4=NULL,rule_id=NULL,trace=? WHERE id=?",(err,r['id'])); nexc+=1
      else:c.execute("UPDATE rows SET status='classified',l1=?,l2=?,l3=?,l4=?,rule_id=?,trace=? WHERE id=?",(rule['l1'],rule['l2'],rule['l3'],rule['l4'],rule['id'],f"{rule['name']} [{rule['field']} {rule['operator']} {rule['pattern']}]; priority {rule['priority']}",r['id'])); nclass+=1
     audit(c,'bulk_classify','file',fid or 'all',{'activeRows':len(rows),'classified':nclass,'exceptions':nexc})
    return self.send_json({'classified':nclass,'exceptions':nexc})
   if path=='/api/resolve':
    rid=int(data['rowId']); levels=[str(data.get(f'l{i}','')).strip() for i in range(1,5)]; actor=str(data.get('reviewer','Local SME')); note=str(data.get('note','SME decision'))
    if not levels[0] or any(not levels[i] and any(levels[j] for j in range(i+1,4)) for i in range(4)):return self.send_json({'error':'Provide a contiguous path under Client Level 1.'},400)
    with connect() as c:
     if not c.execute('SELECT 1 FROM taxonomy WHERE approved=1 AND l1=? AND l2=? AND l3=? AND l4=?',levels).fetchone():return self.send_json({'error':'Manual decisions must use a path in the approved taxonomy.'},400)
     c.execute("UPDATE rows SET status='classified',l1=?,l2=?,l3=?,l4=?,rule_id=NULL,trace='Manual SME decision',decision_by=?,decision_note=? WHERE id=?",(*levels,actor,note,rid)); audit(c,'manual_classification','row',rid,{'path':levels,'note':note},actor)
    return self.send_json({'ok':True})
   if path=='/api/review':
    rid=int(data['rowId']); expected=[str(data.get(f'l{i}','')).strip() for i in range(1,5)]; correct=bool(data.get('correct')); reviewer=str(data.get('reviewer','Local SME'))
    with connect() as c:
     row=c.execute('SELECT * FROM rows WHERE id=?',(rid,)).fetchone()
     if not row:return self.send_json({'error':'Row not found'},404)
     actual=[row[f'l{i}'] or '' for i in range(1,5)]; exp=' > '.join(x for x in expected if x); act=' > '.join(x for x in actual if x)
     c.execute('UPDATE rows SET reviewed=1,correct=? WHERE id=?',(int(correct),rid)); c.execute('INSERT INTO spotchecks(file_id,row_id,expected,actual,correct,reviewer,at) VALUES(?,?,?,?,?,?,?)',(row['file_id'],rid,exp or act,act,int(correct),reviewer,now())); audit(c,'spot_check','row',rid,{'expected':exp,'actual':act,'correct':correct},reviewer)
    return self.send_json({'ok':True})
   if path=='/api/approve-classifications':
    fid=data.get('fileId')
    with connect() as c:
     sql="UPDATE rows SET status='approved' WHERE status='classified'"; args=()
     if fid:sql+=' AND file_id=?';args=(fid,)
     c.execute(sql,args); audit(c,'approve_classifications','file',fid or 'all',{'approved':c.total_changes})
    return self.send_json({'ok':True})
   return self.send_json({'error':'Not found'},404)
  except Exception as e:return self.send_json({'error':str(e)},400)
 def do_PUT(self):self.send_json({'error':'Unsupported'},405)
 def do_HEAD(self):self.send_response(200); self.end_headers()
 def do_OPTIONS(self):self.send_response(204); self.end_headers()
 def do_DELETE(self):
  path=urlparse(self.path).path
  match=re.fullmatch(r'/api/rules/([a-f0-9]+)',path)
  if not match:return self.send_json({'error':'Not found'},404)
  rid=match.group(1)
  try:
   with connect() as c:
    rule=c.execute('SELECT * FROM rules WHERE id=?',(rid,)).fetchone()
    if not rule:return self.send_json({'error':'Rule not found'},404)
    audit(c,'delete','rule',rid,dict(rule));c.execute('DELETE FROM rules WHERE id=?',(rid,))
   return self.send_json({'deleted':True,'id':rid})
  except Exception as e:return self.send_json({'error':str(e)},400)
def serve_export(h):
 q=__import__('urllib.parse').parse.parse_qs(urlparse(h.path).query); fid=q.get('fileId',[''])[0]; fmt=q.get('format',['csv'])[0]
 with connect() as c:
  rows=[dict(r) for r in c.execute("SELECT files.name,rows.* FROM rows JOIN files ON files.id=rows.file_id WHERE (?='' OR file_id=?) ORDER BY files.name,source_row",(fid,fid))]
 headers=['Source File','Source Row','SKU','Description','MPN','Product Series','Product Type Code','Active','Sales','Level 1','Level 2','Level 3','Level 4','Classification Status','Rule ID','Rule Trace','SME Reviewer','SME Note']
 keys=['name','source_row','sku','description','mpn','series','type_code','active','sales','l1','l2','l3','l4','status','rule_id','trace','decision_by','decision_note']
 out=io.StringIO(newline=''); w=csv.writer(out); w.writerow(headers)
 for r in rows:w.writerow([r[k] if k!='active' else ('Yes' if r[k] else 'No') for k in keys])
 b=out.getvalue().encode('utf-8-sig')
 if fmt=='xlsx':
  import openpyxl
  wb=openpyxl.Workbook(write_only=True);ws=wb.create_sheet('Classified SKUs');ws.append(headers)
  for r in rows:ws.append([r[k] if k!='active' else ('Yes' if r[k] else 'No') for k in keys])
  buff=io.BytesIO();wb.save(buff);b=buff.getvalue(); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'; ext='xlsx'
 else:mime='text/csv; charset=utf-8';ext='csv'
 h.send_response(200);h.send_header('Content-Type',mime);h.send_header('Content-Disposition',f'attachment; filename="eis-classified.{ext}"');h.send_header('Content-Length',str(len(b)));h.end_headers();h.wfile.write(b)
def main():
 init()
 # Route export before generic API requests.
 orig=Handler.do_GET
 def get(self):
  if urlparse(self.path).path=='/api/export':return serve_export(self)
  return orig(self)
 Handler.do_GET=get
 host=os.environ.get('EIS_HOST','127.0.0.1');port=int(os.environ.get('EIS_PORT','8765'))
 print(f'EIS Taxonomy Manager running at http://{host}:{port}');ThreadingHTTPServer((host,port),Handler).serve_forever()
if __name__=='__main__':main()
