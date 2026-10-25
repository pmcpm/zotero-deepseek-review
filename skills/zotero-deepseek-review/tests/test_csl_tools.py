import copy, importlib.util, json, subprocess, unittest, os, shutil
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('csl_tools',ROOT/'scripts/csl_tools.py')
csl=importlib.util.module_from_spec(spec);spec.loader.exec_module(csl)
SOURCE='''<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0" class="in-text"><info><title>Test</title><id>http://example.org/base</id><rights license="https://creativecommons.org/licenses/by-sa/3.0/">CC BY-SA</rights></info><locale xml:lang="zh"><terms><term name="page-range-delimiter">～</term></terms></locale><citation><layout><text variable="citation-number"/></layout></citation><bibliography second-field-align="flush"><layout><text variable="citation-number" suffix=". "/><text variable="title"/><choose><if variable="DOI"><text variable="DOI" prefix="https://doi.org/"/></if><else><text variable="URL"/></else></choose><group><text value="DOI:"/><text variable="DOI"/></group></layout></bibliography></style>'''

class StyleTests(unittest.TestCase):
    def test_independent_clone_and_source_preservation(self):
        text,policy=csl.make_style(SOURCE,'task1',hide_doi=True,bilingual=True)
        root=ET.fromstring(text)
        self.assertEqual(root.findtext('c:info/c:id',namespaces=csl.N),policy['style_id'])
        self.assertNotEqual(policy['style_id'],'http://example.org/base')
        self.assertEqual(root.find('c:info/c:rights',csl.N).get('license'),'https://creativecommons.org/licenses/by-sa/3.0/')
        self.assertIn('DOI',SOURCE)
        outputs=[n.get('variable') for n in root.iter() if n.tag in {csl.Q('text'),csl.Q('label'),csl.Q('number')}]
        self.assertNotIn('DOI',outputs);self.assertIn('URL',outputs)
        # The DOI conditional survives so missing DOI selects the URL fallback.
        self.assertIsNotNone(root.find('.//c:if[@variable="DOI"]',csl.N))
        self.assertNotIn('DOI:',text)
        self.assertEqual(root.find('c:bibliography/c:layout',csl.N)[-1].get('variable'),'annote')
        for locale in root.findall('c:locale',csl.N):
            names={n.get('name'):n.text for n in locale.findall('c:terms/c:term',csl.N)}
            for name in ['page-range-delimiter','citation-range-delimiter','year-range-delimiter']:self.assertEqual(names[name],'-')

    def test_bilingual_english_render_removes_duplicate_number(self):
        text,policy=csl.make_style(SOURCE,'task1',bilingual=True,english_render=True)
        root=ET.fromstring(text)
        self.assertFalse(root.findall('.//*[@variable="annote"]'))
        self.assertFalse(root.findall('.//*[@variable="citation-number"]'))
        self.assertEqual(root.get('default-locale'),'en-US')
        self.assertNotIn('second-field-align',root.find('c:bibliography',csl.N).attrib)
        self.assertFalse(policy['bilingual'])

    def test_bilingual_terminal_punctuation_precedes_translation(self):
        source=SOURCE.replace('<bibliography second-field-align="flush"><layout>','<bibliography second-field-align="flush"><layout suffix=".">')
        text,_=csl.make_style(source,'punctuation',bilingual=True)
        layout=ET.fromstring(text).find('c:bibliography/c:layout',csl.N)
        self.assertNotIn('suffix',layout.attrib)
        self.assertEqual(layout[0].get('variable'),'citation-number')
        self.assertEqual(layout[1].tag,csl.Q('group'));self.assertEqual(layout[1].get('suffix'),'.')
        self.assertEqual(layout[2].get('variable'),'annote');self.assertEqual(layout[2].get('suffix'),')')

    def test_fingerprints_change_with_options_and_source(self):
        _,a=csl.make_style(SOURCE,'one')
        _,b=csl.make_style(SOURCE,'one',hide_doi=True)
        _,d=csl.make_style(SOURCE.replace('Test','Changed'),'one')
        self.assertNotEqual(a['style_fingerprint'],b['style_fingerprint'])
        self.assertNotEqual(a['base_source_sha256'],d['base_source_sha256'])
        self.assertEqual(csl.make_style(SOURCE,'one')[1],a)

    def test_filter_preserves_original_and_legitimate_urls(self):
        for doi_url in ['https://doi.org/10.1000/abc','http://dx.doi.org/10.1000/abc','doi:10.1000/abc','10.1000/abc','doi.org/10.1000/abc']:
            source={'DOI':'10.1000/abc','URL':doi_url,'title':'T'}
            output=csl.filter_item_data(source,True)
            self.assertNotIn('URL',output);self.assertNotIn('DOI',output)
            self.assertEqual(source['DOI'],'10.1000/abc')
        for ordinary in ['https://publisher.example/article/10.1000/abc','https://doi.org.example.org/item','https://repository.example/paper.pdf']:
            self.assertEqual(csl.filter_item_data({'DOI':'10.1000/abc','URL':ordinary},True),{'URL':ordinary})
        self.assertEqual(csl.filter_item_data({'DOI':'10.1000/abc'},False),{'DOI':'10.1000/abc'})
        hack={'note':'DOI: 10.1000/abc\nURL: https://doi.org/10.1000/abc\nURL: https://repository.example/a\nA useful note'}
        self.assertEqual(csl.filter_item_data(hack,True)['note'],'URL: https://repository.example/a\nA useful note')

    def test_resource_only_url_policy_and_doi_are_independent(self):
        for kind in ['article-journal','paper-conference','book','chapter','report','thesis','patent','article-newspaper']:
            item={'type':kind,'title':'T','URL':'https://publisher.example/fulltext','DOI':'10.1000/test','issued':{'date-parts':[[2024]]},'accessed':{'date-parts':[[2026,10,25]]},'note':'URL: https://publisher.example/fulltext\nAccessed: 2026-10-25\nUseful note'}
            original=copy.deepcopy(item)
            for hide in [True,False]:
                out=csl.filter_item_data(item,hide,url_policy='resource_only')
                self.assertNotIn('URL',out);self.assertNotIn('accessed',out);self.assertNotIn('URL:',out['note']);self.assertNotIn('Accessed:',out['note'])
                self.assertEqual(out['issued'],item['issued']);self.assertEqual('DOI' in out,not hide)
            self.assertEqual(item,original)
        for kind in ['webpage','post','post-weblog','dataset','software']:
            item={'type':kind,'URL':'https://agency.example/notice','issued':{'date-parts':[[2025,3,1]]},'accessed':{'date-parts':[[2026,10,25]]}}
            self.assertEqual(csl.filter_item_data(item,True,url_policy='resource_only'),item)
        for item in [{'type':'report','genre':'EB/OL'},{'type':'document'}]:
            item.update(URL='https://agency.example/announcement',accessed={'date-parts':[[2026,10,25]]})
            self.assertEqual(csl.filter_item_data(item,url_policy='resource_only',url_required=True),item)
        doi={'type':'article-journal','URL':'https://doi.org/10.1000/test','DOI':'10.1000/test'}
        self.assertIn('URL',csl.filter_item_data(doi,url_policy='resource_only'))
        self.assertNotIn('URL',csl.filter_item_data(doi,True,url_policy='resource_only'))
        _,default=csl.make_style(SOURCE,'url-default');_,explicit=csl.make_style(SOURCE,'url-default',url_policy='all')
        self.assertEqual(default['url_policy'],'resource_only');self.assertNotEqual(default['style_id'],explicit['style_id'])

    def test_ascii_display_preserves_metadata_and_identifiers(self):
        source={'id':'https://example.org/item–1','title':'产业–需求—匹配','container-title':'Journal–Testing',
                'page':'12–18','DOI':'10.1000/a–b','URL':'https://example.org/a–b','uri':'urn:test:a–b',
                'author':[{'family':'Example–Test','given':'A'}],
                'annote':'English–entry https://example.org/a–b 10.1000/a–b',
                'note':'title: Note–title\nDOI: 10.1000/a–b\nURL: https://example.org/a–b'}
        original=copy.deepcopy(source);display=csl.filter_item_data(source)
        self.assertEqual(source,original)
        for field in ['id','DOI','URL','uri']:self.assertEqual(display[field],source[field])
        self.assertEqual(display['title'],'产业-需求-匹配');self.assertEqual(display['page'],'12-18')
        self.assertEqual(display['author'][0]['family'],'Example-Test')
        self.assertEqual(display['annote'],'English-entry https://example.org/a–b 10.1000/a–b')
        self.assertIn('title: Note-title',display['note']);self.assertIn('DOI: 10.1000/a–b',display['note'])
        styled=SOURCE.replace('<text variable="title"/>','<text variable="title"/><text value="label–value"/>').replace('</terms>','<term name="and">label–term</term></terms>')
        text,_=csl.make_style(styled,'ascii-test')
        self.assertIn('value="label-value"',text)
        self.assertIn('>label-term</',text)

    def test_rejects_dependent_style_and_bad_task_ids(self):
        with self.assertRaises(ValueError):csl.make_style('<style xmlns="'+csl.CSL+'"><info/></style>','x')
        with self.assertRaises(ValueError):csl.make_style(SOURCE,'../other')

    def test_runtime_scoping_and_restore(self):
        node=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        if not node.exists():node=Path(os.environ.get('NODE_BINARY') or shutil.which('node') or '__missing_node__')
        if not node.exists():self.skipTest('Node unavailable')
        js=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const live={title:'Original',version:1,dateModified:'2026-01-01',tags:[],collections:[]};
const item={key:'K1',toJSON:()=>live};
const originalData={id:1,title:'Original',DOI:'10.1000/x',URL:'https://doi.org/10.1000/x'};
const fullItemData={...originalData};delete originalData.URL;
function System(){} System.prototype.retrieveItem=()=>originalData;
function Engine(sys,xml,locale){this.sys=sys;this.xml=xml;this.locale=locale;this.opt={development_extensions:{}};this.setOutputFormat=x=>this.format=x;this.updateItems=()=>{};this.makeBibliography=()=>[{},[this.format==='html'?'<div class="csl-entry">Author. <i>Journal</i>, <i>10</i>.</div>':'Author. Journal, 10.']];}
function Item(){};function Style(id){this.styleID=id;this.getXML=()=>'<style/>';}
const original=function(){return {unmanaged:true}};Style.prototype.getCiteProc=original;
const ctx={URL,JSON,Zotero:{Item,Style,Items:{get:()=>item},Utilities:{Item:{itemToCSLJSON:()=>fullItemData}},Cite:{System},CiteProc:{CSL:{Engine}}}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
const c=ctx.CodexReviewCSL;
const policy={hide_doi:true,bilingual:true,style_fingerprint:'s',english_entries:{K1:{text:'English entry',style_fingerprint:'s',item_snapshot:c.metadataSnapshot(item.toJSON())}}};
const restore=c.installHooks(id=>id==='managed'?policy:null);
assert(new Style('other').getCiteProc().unmanaged);
let e=new Style('managed').getCiteProc('zh-CN','text');let d=e.sys.retrieveItem(1);
assert(!d.DOI&&!d.URL);assert.equal(d.annote,'English entry');assert(originalData.DOI);assert(!originalData.annote);
live.version=2;live.dateModified='2026-03-01';live.tags=[{tag:'another task'}];live.collections=['OTHER'];
d=e.sys.retrieveItem(1);assert.equal(d.annote,'English entry');
live.title='Changed metadata';d=e.sys.retrieveItem(1);assert(!d.annote);
live.title='Original';
policy.english_entries.K1.style_fingerprint='old';d=e.sys.retrieveItem(1);assert(!d.annote);
policy.english_entries.K1.style_fingerprint='s';policy.english_entries.K1.item_snapshot='stale';d=e.sys.retrieveItem(1);assert(!d.annote);
assert.equal(c.filterItemData({URL:'https://publisher.example/10.1000/x'},policy).URL,'https://publisher.example/10.1000/x');
const rawDisplay={title:'A–B',page:'12–18',author:[{family:'Example–Test'}],DOI:'10.1000/a–b',URL:'https://example.org/a–b',annote:'English–entry 10.1000/a–b'};
const display=c.filterItemData(rawDisplay,{});assert.equal(display.title,'A-B');assert.equal(display.page,'12-18');assert.equal(display.author[0].family,'Example-Test');assert.equal(rawDisplay.title,'A–B');assert.equal(display.DOI,rawDisplay.DOI);assert.equal(display.URL,rawDisplay.URL);assert.equal(display.annote,'English-entry 10.1000/a–b');
fullItemData.URL='https://publisher.example/article';fullItemData.accessed={'date-parts':[[2026,10,25]]};
d=e.sys.retrieveItem(1);assert.equal(d.URL,fullItemData.URL);assert.equal(d.accessed['date-parts'][0][0],2026);assert(!originalData.URL);
policy.english_entries.K1.item_snapshot=c.metadataSnapshot(item.toJSON());policy.english_entries.K1.inline_html='<i onclick="bad()">English</i> entry<script>bad()</script>';
d=e.sys.retrieveItem(1);assert.equal(d.annote,'<i>English</i> entry');
const rendered=c.renderEnglish('<style/>',{title:'Audited title'},{});assert.equal(rendered.text,'Author. Journal, 10.');assert.equal(rendered.inline_html,'Author. <i>Journal</i>, <i>10</i>.');assert(!rendered.inline_html.includes('div'));
assert.equal(c.sanitizeInlineHTML('<div><b bad="x">Bold</b><a href="javascript:x">plain</a><iframe>bad</iframe></div>'),'<b>Bold</b>plain');
assert.equal(c.sanitizeInlineHTML('<div>A &#38; B &amp; C</div>'),'A & B & C');
for(const type of ['article-journal','paper-conference','book','chapter','report','thesis','patent']){
 const source={type,URL:'https://publisher.example/fulltext',DOI:'10.1000/test',issued:{'date-parts':[[2024]]},accessed:{'date-parts':[[2026,10,25]]},note:'URL: https://publisher.example/fulltext\nAccessed: 2026-10-25\nUseful note'};
 for(const hide_doi of [false,true]){const out=c.filterItemData(source,{url_policy:'resource_only',hide_doi});assert(!out.URL&&!out.accessed);assert.equal(!!out.DOI,!hide_doi);assert(!out.note.includes('URL:'));assert(!out.note.includes('Accessed:'));assert(out.issued);}
 assert(source.URL&&source.accessed);
}
const webpage={type:'webpage',URL:'https://agency.example/notice',issued:{'date-parts':[[2025,3,1]]},accessed:{'date-parts':[[2026,10,25]]}};
assert.deepEqual(c.filterItemData(webpage,{url_policy:'resource_only',hide_doi:true}),webpage);
assert(c.filterItemData({...webpage,type:'report',genre:'EB/OL'},{url_policy:'resource_only'}).URL);
assert(c.filterItemData({...webpage,type:'report'},{url_policy:'resource_only',url_required:true}).URL);
restore();assert.equal(Style.prototype.getCiteProc,original);
process.stdout.write('runtime invariants passed');
'''
        completed=subprocess.run([str(node),'-e',js,str(ROOT/'assets/zotero-bridge/csl-runtime.js')],capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)

if __name__=='__main__':unittest.main()
