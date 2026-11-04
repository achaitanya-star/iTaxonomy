import json, tempfile, unittest, os
import io
from pathlib import Path
import server
import db

class EngineChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        server.DB=Path(self.tmp.name)/'test.db'
        server.init()
    def tearDown(self): self.tmp.cleanup()
    def test_vercel_without_postgres_fails_clearly_without_writing_to_readonly_root(self):
        names=(*db.POSTGRES_ENV_NAMES,'VERCEL')
        previous={name:os.environ.get(name) for name in names}
        try:
            for name in db.POSTGRES_ENV_NAMES:os.environ.pop(name,None)
            os.environ['VERCEL']='1'
            blocked=Path(self.tmp.name)/'readonly-root'/'eis.db'
            with self.assertRaisesRegex(RuntimeError,'PostgreSQL is not connected'):
                db.connect(blocked)
            self.assertFalse(blocked.parent.exists())
        finally:
            for name,value in previous.items():
                if value is None:os.environ.pop(name,None)
                else:os.environ[name]=value
    def test_marketplace_prefixed_postgres_url_is_detected(self):
        names=(*db.POSTGRES_ENV_NAMES,'PURPLEFIELD_DATABASE_URL','PURPLEFIELD_POSTGRES_URL')
        previous={name:os.environ.get(name) for name in names}
        try:
            for name in names:os.environ.pop(name,None)
            os.environ['PURPLEFIELD_DATABASE_URL']='postgresql://example.invalid/db'
            self.assertEqual(db.postgres_url(),'postgresql://example.invalid/db')
            os.environ.pop('PURPLEFIELD_DATABASE_URL')
            os.environ['PURPLEFIELD_POSTGRES_URL']='postgresql://example.invalid/other'
            self.assertEqual(db.postgres_url(),'postgresql://example.invalid/other')
        finally:
            for name,value in previous.items():
                if value is None:os.environ.pop(name,None)
                else:os.environ[name]=value
    def test_csv_upload_and_detection(self):
        rows,kind=server.read_upload('sample.csv',b'SKU,Description,MPN,Active,Sales\n1,Bearing,PN-1,Yes,125.50\n')
        self.assertEqual(kind,'CSV'); self.assertEqual(rows[0]['SKU'],'1')
        self.assertEqual(server.pick(list(rows[0]),'sku'),'SKU')
        self.assertEqual(server.pick(list(rows[0]),'description'),'Description')
        client_headers=['PRODUCTKEY','PRODUCTID','PRODUCTNAME','Activebit','ACTIVESALES']
        self.assertEqual(server.pick(client_headers,'sku'),'PRODUCTID')
        self.assertEqual(server.pick(client_headers,'description'),'PRODUCTNAME')
        self.assertEqual(server.pick(client_headers,'active'),'Activebit')
        self.assertEqual(server.pick(client_headers,'sales'),'ACTIVESALES')
        client_headers=['PRODUCTKEY','PRODUCTID','PRODUCTNAME','Activebit','ACTIVESALES']
        self.assertEqual(server.pick(client_headers,'sku'),'PRODUCTID')
        self.assertEqual(server.pick(client_headers,'description'),'PRODUCTNAME')
        self.assertEqual(server.pick(client_headers,'active'),'Activebit')
        self.assertEqual(server.pick(client_headers,'sales'),'ACTIVESALES')
    def test_xlsx_upload(self):
        import openpyxl
        wb=openpyxl.Workbook();ws=wb.active;ws.append(['SKU','Description','MPN']);ws.append(['A-1','Bearing','PN-A'])
        payload=io.BytesIO();wb.save(payload)
        rows,kind=server.read_upload('sample.xlsx',payload.getvalue())
        self.assertEqual(kind,'Excel');self.assertEqual(rows[0]['MPN'],'PN-A')
    def test_taxonomy_and_rule_import_stay_pending(self):
        count=server.import_taxonomy_records([{'Level 1':'CLIENT L1','Level 2':'ENTRY FITTINGS','Level 3':'ANGLED','Level 4':'45 DEGREE','Definition':'Angled cable entry fittings.'}])
        self.assertEqual(count,1)
        count=server.import_rule_records([{'Rule Name':'45 degree fittings','Marker Field':'Description','Operator':'contains','Pattern':'45°','Priority':'100','Level 1':'CLIENT L1','Level 2':'ENTRY FITTINGS','Level 3':'ANGLED','Level 4':'45 DEGREE'}])
        self.assertEqual(count,1)
        state=server.data_state()
        self.assertFalse(state['taxonomy'][0]['approved'])
        self.assertFalse(state['rules'][0]['approved'])
        self.assertIn('Approve the matching',state['rules'][0]['approvalBlock'])
    def test_rule_trace_and_conflict(self):
        row={'sku':'AB-1','description':'deep groove bearing','mpn':'X','series':'6200','type_code':'BRG','data':json.dumps({})}
        a={'id':'a','name':'rule a','field':'description','operator':'contains','pattern':'bearing','priority':10,'enabled':1,'approved':1,'l1':'BEARINGS','l2':'BALL BEARINGS','l3':'DEEP GROOVE','l4':''}
        b={**a,'id':'b','name':'rule b','l3':'OTHER'}
        rule,error=server.classify_row(row,[a]);self.assertEqual(rule['id'],'a');self.assertIsNone(error)
        rule,error=server.classify_row(row,[a,b]);self.assertIsNone(rule);self.assertIn('Conflicting',error)
        b['priority']=20;rule,error=server.classify_row(row,[b,a]);self.assertEqual(rule['id'],'a');self.assertIsNone(error)
    def test_empty_database_metrics_and_demo_is_not_seeded(self):
        state=server.data_state();self.assertEqual(state['files'],[]);self.assertEqual(state['taxonomy'],[]);self.assertEqual(state['rules'],[])
        self.assertEqual(state['metrics']['ruleCoverage'],0)
        self.assertIsNone(state['metrics']['spotCheckAccuracy'])
    def test_file_quality_counts_and_active_filter(self):
        with server.connect() as c:
            c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)',('f1','sample.csv','2026-01-01',2,'SKU','Description',None,None,None,None,'Sales'))
            c.execute("INSERT INTO rows(file_id,source_row,sku,description,active,sales,status,rule_id) VALUES('f1',2,'1','bearing',1,25,'classified','r1')")
            c.execute("INSERT INTO rows(file_id,source_row,sku,description,active,sales,status) VALUES('f1',3,'2','inactive',0,0,'unclassified')")
            c.execute("INSERT INTO spotchecks(file_id,row_id,expected,actual,correct,reviewer,at) VALUES('f1',1,'BEARINGS','BEARINGS',1,'SME','2026-01-01')")
        state=server.data_state()
        self.assertEqual(state['metrics']['activeRows'],1)
        self.assertEqual(state['metrics']['ruleCoverage'],100)
        self.assertEqual(state['metrics']['spotCheckAccuracy'],100)
        self.assertEqual(state['qualityByFile'][0]['spotChecks'],1)
    def test_similarity_recommendation_is_based_on_same_file_example(self):
        with server.connect() as c:
            c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)',('f1','sample.csv','2026-01-01',2,'SKU','Description',None,None,None,None,None))
            c.execute("INSERT INTO rows(file_id,source_row,sku,description,active,status,l1,l2,l3,l4) VALUES('f1',2,'A','SKF deep groove ball bearing',1,'classified','BEARINGS','BALL BEARINGS','DEEP GROOVE BALL BEARINGS','')")
            c.execute("INSERT INTO rows(file_id,source_row,sku,description,mpn,active,status) VALUES('f1',3,'B','SKF deep groove ball bearing 6206','6206',1,'exception')")
        with server.connect() as c: marker_suggestions,recommendations=server.discover(c)
        self.assertEqual(len(recommendations),1)
        self.assertEqual(recommendations[0]['rowId'],2)
        self.assertEqual(recommendations[0]['l3'],'DEEP GROOVE BALL BEARINGS')
    def test_repeated_marker_suggestion(self):
        with server.connect() as c:
            c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)',('f1','sample.csv','2026-01-01',2,'SKU','Description',None,None,None,None,None))
            c.execute("INSERT INTO rows(file_id,source_row,sku,description,series,type_code,active,status) VALUES('f1',2,'A','Bearing 6205','6200','BRG',1,'unclassified')")
            c.execute("INSERT INTO rows(file_id,source_row,sku,description,series,type_code,active,status) VALUES('f1',3,'B','Bearing 6206','6200','BRG',1,'unclassified')")
        with server.connect() as c: suggestions,recommendations=server.discover(c)
        self.assertTrue(any(x['field']=='series' and x['pattern']=='6200' and x['count']==2 for x in suggestions))
        state=server.data_state()
        self.assertTrue(any(x['pattern']=='6200' for x in state['suggestions']))
    def test_pending_rule_explains_missing_taxonomy_path(self):
        with server.connect() as c:
            c.execute("INSERT INTO rules VALUES('r1','rule','description','contains','marker','CABLE MANAGEMENT & PROTECTION','','','',100,1,0,'2026-01-01')")
        state=server.data_state()
        self.assertIn('Add and approve',state['rules'][0]['approvalBlock'])
        with server.connect() as c:
            c.execute("INSERT INTO taxonomy VALUES('t1','CABLE MANAGEMENT & PROTECTION','','','', 'Draft definition',0)")
        state=server.data_state()
        self.assertIn('Approve the matching',state['rules'][0]['approvalBlock'])

if __name__=='__main__': unittest.main(verbosity=2)
