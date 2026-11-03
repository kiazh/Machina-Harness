import {useCallback,useEffect,useRef,useState,useSyncExternalStore} from 'react';
import type {DesktopController} from '../../state/controller.ts';

export function useEnginePage<T>(controller:DesktopController,method:string,sessionRequired=false) {
 const state=useSyncExternalStore(controller.subscribe,controller.snapshot);
 const sessionId=state.session?.id;
 const supported=(name:string)=>!state.methods?.size||state.methods.has(name);
 const unavailableReason=state.connection!=='ready'?(state.connection==='connecting'?'Connecting the local engine…':'Connect the local engine to use these controls.'):state.switchingSession||state.switchingWorkspace?'Wait for the conversation or workspace to finish opening.':!supported(method)?'This engine version does not support these controls. Update the local engine.':sessionRequired&&!sessionId?'Open a conversation first to use these controls.':'';
 const [data,setData]=useState<T>(),[busy,setBusy]=useState(false),[working,setWorking]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[revision,setRevision]=useState(0);
 const alive=useRef(false),scope=useRef(sessionId),inFlight=useRef(false);scope.current=sessionId;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 const refresh=useCallback(()=>setRevision(value=>value+1),[]);
 const dataScope=useRef('');
 useEffect(()=>{
  let active=true;setError('');
  const key=`${method}:${sessionId??''}`;
  if(dataScope.current!==key){setData(undefined);setNotice('');dataScope.current=key;}
  if(unavailableReason){setBusy(false);setData(undefined);return;}
  setBusy(true);
  void controller.bridge.request<T>(method,{...(sessionId?{sessionId}:{}),limit:100}).then(result=>{if(active)setData(result)}).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:String(reason))}).finally(()=>{if(active)setBusy(false)});
  return()=>{active=false};
 },[controller,method,sessionId,state.connection,revision,unavailableReason]);
 async function act<R=Record<string,unknown>>(name:string,params:Record<string,unknown>,success?:string):Promise<R|undefined>{
  if(controller.snapshot().backgroundOperation&&!['approvals.resolve','mode.revoke'].includes(name)){setError('Wait for the background run to settle, or stop it first.');return}
  if(inFlight.current)return;const captured=sessionId;if(unavailableReason){setError(unavailableReason);return}if(!supported(name)){setError('This engine version does not support this action. Update the local engine.');return}
  inFlight.current=true;setWorking(true);setError('');setNotice('');
  try{const result=await controller.bridge.request<R>(name,{...(captured?{sessionId:captured}:{}),...params});if(!alive.current||scope.current!==captured)return;if(success)setNotice(success);refresh();return result}catch(reason){if(alive.current&&scope.current===captured)setError(reason instanceof Error?reason.message:String(reason));return}finally{inFlight.current=false;if(alive.current)setWorking(false)}
 }
 return {state,sessionId,data,busy,working:working||!!state.backgroundOperation,error,notice,setNotice,refresh,act,unavailableReason,available:!unavailableReason};
}
