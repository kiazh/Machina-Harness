import {useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {ArrowUpRight,RefreshCw,Search,X} from 'lucide-react';
import type {MarketplaceDiscovery,MarketplaceSearch,MarketplaceSkill,MarketplaceSource} from '@machina/contracts/rpc';
import type {DesktopController} from '../../state/controller.ts';
import {Select} from '../../components/Select.tsx';
import './marketplace.css';

const label=(market:string)=>market==='skillsmp'?'SkillsMP':market;
const count=(value:number)=>new Intl.NumberFormat(undefined,{notation:value>=10000?'compact':'standard',maximumFractionDigits:1}).format(value);
const message=(error:unknown)=>error instanceof Error?error.message:String(error);

function Metrics({skill}:{skill:MarketplaceSkill}){
 const metrics=skill.metrics??(typeof skill.installs==='number'?[{market:'skills.sh',label:'installs',value:skill.installs}]:[]);
 return <span className="marketplace-metrics">{metrics.length?metrics.map(metric=><span key={metric.market} title={`${metric.value.toLocaleString()} ${metric.label} on ${label(metric.market)}`}><b>{count(metric.value)}</b> {metric.label}</span>):<span>No popularity data</span>}</span>;
}
function SkillRow({skill,rank,busy,canReview,onReview}:{skill:MarketplaceSkill;rank?:number;busy:boolean;canReview:boolean;onReview:(skill:MarketplaceSkill)=>void}){
 return <article className="marketplace-skill">
  {rank!==undefined&&<span className="marketplace-rank" aria-hidden="true">{String(rank).padStart(2,'0')}</span>}
  <div className="marketplace-skill-copy"><h3>{skill.name}</h3><p title={skill.source}>{skill.source}</p>{skill.description&&<p className="marketplace-description">{skill.description}</p>}<Metrics skill={skill}/></div>
  <button className="marketplace-review" disabled={busy||!canReview} aria-label={`Review ${skill.name}`} title={canReview?'Read the exact files before installing':'Open a conversation to review and install'} onClick={()=>onReview(skill)}><span>Review</span><ArrowUpRight size={13}/></button>
 </article>;
}

export function Marketplace({controller,busy,onReview}:{controller:DesktopController;busy:boolean;onReview:(skill:MarketplaceSkill)=>void}){
 const state=useSyncExternalStore(controller.subscribe,controller.snapshot);
 const [discovery,setDiscovery]=useState<MarketplaceDiscovery>(),[results,setResults]=useState<MarketplaceSearch>(),[query,setQuery]=useState(''),[source,setSource]=useState('all'),[loading,setLoading]=useState(false),[error,setError]=useState(''),[revision,setRevision]=useState(0);
 const generation=useRef(0),scope=useRef(''),loadingRef=useRef(false);
 const identity=`${state.connection}:${state.session?.id??''}:${state.workspace?.workspaceId??''}`;
 if(scope.current!==identity){scope.current=identity;generation.current++;loadingRef.current=false}
 const supported=!state.methods?.size||state.methods.has('skills.marketplace.discover');
 useEffect(()=>{
  if(state.connection!=='ready'||!supported)return;
  const current=++generation.current;setLoading(true);loadingRef.current=true;setError('');setResults(undefined);setDiscovery(undefined);
  void controller.bridge.request<MarketplaceDiscovery>('skills.marketplace.discover',{limit:10}).then(value=>{if(current===generation.current)setDiscovery(value)}).catch(reason=>{if(current===generation.current)setError(message(reason))}).finally(()=>{if(current===generation.current){setLoading(false);loadingRef.current=false}});
  return()=>{generation.current++;loadingRef.current=false};
 },[controller,identity,revision,supported]);
 async function search(term=query,catalog=source){
  const trimmed=term.trim();if(loadingRef.current||trimmed.length<2||state.connection!=='ready')return;
  const current=++generation.current;loadingRef.current=true;setLoading(true);setError('');setQuery(term);
  try{const value=await controller.bridge.request<MarketplaceSearch>('skills.marketplace.search',{query:trimmed,limit:30,markets:catalog==='all'?['skills.sh','skillsmp']:[catalog]});if(current===generation.current)setResults(value)}catch(reason){if(current===generation.current)setError(message(reason))}finally{if(current===generation.current){loadingRef.current=false;setLoading(false)}}
 }
 const markets:MarketplaceSource[]=results?.markets??discovery?.markets??[];
 const allowed=(skill:MarketplaceSkill)=>source==='all'||(skill.markets??['skills.sh']).includes(source);
 const filtered=results?.skills.filter(allowed);
 const unavailable=markets.filter(market=>market.status==='unavailable');
 const allUnavailable=markets.length>0&&unavailable.length===markets.length;
 const renderSkill=(skill:MarketplaceSkill,rank?:number)=><SkillRow key={skill.id} skill={skill} rank={rank} busy={busy} canReview={!!state.session} onReview={onReview}/>;
 return <section className="marketplace" aria-label="Skills marketplace">
  <div className="marketplace-heading"><div><h1>Discover skills</h1><p>Browse workflows from skills.sh and SkillsMP.</p></div><button className="marketplace-refresh" aria-label="Refresh skill catalogs" disabled={loading} onClick={()=>{setQuery('');setRevision(value=>value+1)}}><RefreshCw size={14}/></button></div>
  <form className="marketplace-search" onSubmit={event=>{event.preventDefault();void search()}}>
   <label className="marketplace-search-input"><Search size={16}/><input aria-label="Search marketplace" placeholder="Search skills for the work you do…" value={query} onChange={event=>setQuery(event.target.value)} maxLength={200}/>{query&&<button type="button" aria-label="Clear marketplace search" disabled={loading} onClick={()=>{setQuery('');setResults(undefined)}}><X size={13}/></button>}</label>
   <Select aria-label="Skill catalog" disabled={loading} value={source} onChange={event=>{setSource(event.target.value);if(results)void search(results.query,event.target.value)}}><option value="all">All catalogs</option><option value="skills.sh">skills.sh</option><option value="skillsmp">SkillsMP</option></Select>
   <button className="marketplace-search-button" disabled={loading||query.trim().length<2}>Search</button>
  </form>
  <div className="marketplace-meta"><span>{results?`${filtered?.length??0} ${filtered?.length===1?'match':'matches'} for “${results.query}”`:'skills.sh + SkillsMP'}</span>{results&&<button disabled={loading} onClick={()=>{setQuery('');setResults(undefined)}}>Back to discovery</button>}</div>
  {!results&&<div className="marketplace-topics" aria-label="Explore skill topics">{['Design','Testing','Python','Research','Writing'].map(topic=><button key={topic} disabled={loading} onClick={()=>{void search(topic.toLowerCase())}}>{topic}</button>)}</div>}
  {!supported&&<p role="status">Update the local engine to browse multiple skill catalogs.</p>}
  {loading&&<div className="marketplace-loading" role="status"><span>Loading skill catalogs…</span><i/><i/><i/></div>}
  {error&&<div className="marketplace-source-error" role="alert"><p>{error}</p><button onClick={()=>setRevision(value=>value+1)}>Try again</button></div>}
  {!!unavailable.length&&<div className="marketplace-source-error" role="status">{unavailable.map(market=><p key={market.id}>{market.error||`${market.name} is unavailable.`}</p>)}{!allUnavailable&&<span>Showing the available catalog.</span>}<button disabled={loading} onClick={()=>setRevision(value=>value+1)}>Retry catalogs</button></div>}
  {!loading&&results&&<div className="marketplace-search-results">{filtered?.map(skill=>renderSkill(skill))}{!filtered?.length&&!allUnavailable&&!error&&<p className="catalog-empty">No matching skills. Try another topic or catalog.</p>}</div>}
  {!loading&&!results&&discovery&&<>
   {!!discovery.featured.filter(allowed).length&&<section className="marketplace-featured" aria-label="Featured skills"><div className="marketplace-section-heading"><h2>Featured skills</h2><span>Selected for everyday development</span></div><div>{discovery.featured.filter(allowed).map(skill=><article key={skill.id}><small>{(skill.markets??['skills.sh']).map(label).join(' · ')}</small><h3>{skill.name}</h3><p>{skill.description}</p><div><Metrics skill={skill}/><button disabled={busy||!state.session} aria-label={`Review featured ${skill.name}`} onClick={()=>onReview(skill)}>Read skill <ArrowUpRight size={12}/></button></div></article>)}</div></section>}
   <div className="marketplace-catalogs">{discovery.sections.filter(section=>source==='all'||section.market===source).map(section=><section key={section.market} aria-label={section.title}><div className="marketplace-section-heading"><div><h2>{section.title}</h2><p>{section.description}</p></div><span>{label(section.market)}</span></div>{section.skills.map((skill,index)=>renderSkill(skill,index+1))}{!section.skills.length&&!unavailable.some(row=>row.id===section.market)&&<p className="catalog-empty">No listings returned from this catalog.</p>}</section>)}</div>
  </>}
  {!state.session&&<p className="marketplace-footnote">Open a conversation to review and install skills.</p>}
  <p className="marketplace-footnote">Popularity is reported by each catalog. Installs and repository stars measure different things. You review the files before installation.</p>
 </section>;
}
