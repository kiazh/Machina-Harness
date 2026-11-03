import {expect,it} from 'vitest';
import type {WorkspaceIntelligenceResult} from '@machina/contracts/workspace';
import {inspectionResult} from '../../src/features/settings/lspResults.ts';

const uri='file:///test/main.py';
const range={start:{line:0,character:0},end:{line:1,character:2}};
const inspect=(operation:string,payload:unknown)=>inspectionResult({operation,result:payload,uri} as WorkspaceIntelligenceResult);

it('accepts standard single and array locations and preserves exact zero positions',()=>{
 expect(inspect('definition',{uri,range}).rows[0]?.location).toEqual({uri,position:{line:0,character:0}});
 expect(inspect('references',[{uri,range}]).rows).toHaveLength(1);
 expect(inspect('definition',[{targetUri:uri,targetSelectionRange:{invalid:true},targetRange:range}]).rows[0]?.location?.position).toEqual({line:0,character:0});
});

it('retains malformed locations in the structured fallback without inventing navigation targets',()=>{
 const invalid=[{uri,range:{...range,start:{line:-1,character:0}}},{uri,range:{...range,start:{line:0,character:0.5}}},{uri,range:{start:{line:2,character:0},end:{line:1,character:0}}},{uri,range:{start:{line:0,character:0}}}];
 const result=inspect('references',{locations:[{uri,range},...invalid]});expect(result.rows).toHaveLength(1);expect(result.unsupported).toEqual(invalid);
 const diagnostic=inspect('diagnostics',[{message:'Malformed diagnostic range',range:{start:{line:NaN,character:0},end:{line:1,character:0}}}]);expect(diagnostic.rows[0]?.label).toBe('Malformed diagnostic range');expect(diagnostic.rows[0]?.location).toBeUndefined();
});

it('supports flat normalized symbols and standard SymbolInformation without losing their URI',()=>{
 const symbol={name:'run',kind:12,containerName:'Service',location:{uri:'file:///test/service.py',range}};
 expect(inspect('symbols',[symbol]).rows[0]).toMatchObject({label:'run',kind:'Function',detail:'Service',location:{uri:'file:///test/service.py',position:{line:0,character:0}}});
 expect(inspect('symbols',{flat:[{name:'run',kind:6,uri,range,depth:2}],omitted:4}).rows[0]).toMatchObject({depth:2,kind:'Method',location:{uri,position:{line:0,character:0}}});
 expect(inspect('symbols',{flat:[],omitted:4}).omitted).toBe(4);
});

it('handles MarkupContent, MarkedString, plain text and empty responses without executing markup',()=>{
 expect(inspect('hover',{contents:{kind:'markdown',value:'**run**'}}).hover).toBe('**run**');
 expect(inspect('hover',{contents:{language:'python',value:'def run()'}}).hover).toBe('def run()');
 expect(inspect('hover','plain server information').hover).toBe('plain server information');
 expect(inspect('hover',{contents:{unexpected:true}}).unsupported).toEqual([{contents:{unexpected:true}}]);
 expect(inspect('diagnostics',null).rows).toEqual([]);expect(inspect('symbols',[]).unsupported).toEqual([]);
});

it('bounds visible rows and malformed fallback entries and discloses display truncation',()=>{
 const locations=inspect('references',Array.from({length:1001},()=>({uri,range})));expect(locations.rows).toHaveLength(1000);expect(locations.displayTruncated).toBe(true);
 const malformed=inspect('references',Array.from({length:21},()=>({unexpected:true})));expect(malformed.unsupported).toHaveLength(20);expect(malformed.displayTruncated).toBe(true);
 const children=Array.from({length:1001},(_,index)=>({name:`child ${index}`,range}));const symbols=inspect('symbols',[{name:'parent',range,children}]);expect(symbols.rows).toHaveLength(1000);expect(symbols.displayTruncated).toBe(true);
});
