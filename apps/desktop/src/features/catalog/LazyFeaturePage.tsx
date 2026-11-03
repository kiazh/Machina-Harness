import {lazy,Suspense} from 'react';
import type {ComponentProps} from 'react';
const Page=lazy(()=>import('./FeaturePage.tsx').then(module=>({default:module.FeaturePage})));
export function FeaturePage(props:ComponentProps<typeof Page>){return <Suspense fallback={<section className="feature-page" role="status"><p className="feature-content">Opening page…</p></section>}><Page {...props}/></Suspense>}
