import React from 'react';
import {LoaderCircle,Film} from 'lucide-react';
export function Heading({eyebrow,title,description,children}){
  return <header className="analysis-page-heading workbench-heading"><div><p className="yu-kicker">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div><div className="workbench-actions">{children}</div></header>;
}
export function Empty({title,children}){return <div className="workbench-empty"><Film size={30} strokeWidth={1.3}/><h3>{title}</h3><p>{children}</p></div>;}
export function Loading({text='正在读取…'}){return <p className="workbench-loading" role="status"><LoaderCircle size={17} className="animate-spin"/>{text}</p>;}
export function Notice({error,children}){return children?<p role={error?'alert':'status'} className={error?'analysis-alert':'workbench-notice'}>{children}</p>:null;}
export function Progress({progress}){return progress?<div className="workbench-upload" role="status"><div><strong>{progress.label||'上传视频'}</strong><span>{progress.percent}%</span></div><progress value={progress.percent} max="100"/></div>:null;}
