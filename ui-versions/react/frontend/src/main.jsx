import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AnimatePresence, LayoutGroup, MotionConfig, motion, useReducedMotion} from 'motion/react';
import gsap from 'gsap';
import {useGSAP} from '@gsap/react';
import {ArrowRight, ChevronDown, Film, FolderOpen, LogOut, Menu, X, ChartNoAxesCombined, Scissors, LoaderCircle, ShieldCheck, MessageSquarePlus, BookOpen} from 'lucide-react';
import './styles.css';
import './workspace.css';
import {api,go,loadScript,path} from './lib/api';
import Analysis from './features/analysis/Analysis';
import Library from './features/library/Library';
import Feedback from './features/feedback/Feedback';
import Help from './features/help/Help';
import './features/help/help.css';

gsap.registerPlugin(useGSAP);
const host = document.getElementById('root');
const view = host.dataset.view;
function Mark({large = false}) {
  return <a href={path('/library')} className="flex shrink-0 items-center gap-3 no-underline"><img src={path('/static/yu-mark.svg')} alt="" className={large ? 'size-12' : 'size-10'}/><span className="text-[25px] font-semibold tracking-[.12em] text-ink">羽序<span className="mt-0.5 block text-[9px] font-normal tracking-[.1em] text-muted">YU · YOUR GAME, YOUR GROWTH</span></span></a>;
}
function CourtArt({welcome = false, animated = true}) {
  const scope = useRef(null), reduced = useReducedMotion();
  useGSAP(() => {
    if (reduced || !animated) return;
    gsap.fromTo('.court-line', {strokeDasharray: 700, strokeDashoffset:700}, {strokeDashoffset:0,duration:1.6,stagger:.09,ease:'power2.out'});
    gsap.fromTo('.court-shuttle', {opacity:0,y:-14}, {opacity:1,y:0,duration:.8,delay:.65,ease:'power2.out'});
  }, {scope, dependencies:[reduced,animated]});
  return <div ref={scope} className={welcome ? 'court-art welcome-court' : 'court-art'} aria-hidden="true"><svg viewBox="0 0 360 230" fill="none"><g transform="translate(18 25) rotate(-12 165 90)" stroke="#c6b1e8" strokeWidth="1.3"><path className="court-line" d="M12 20h300v155H12z"/><path className="court-line" d="M32 20v155M292 20v155M12 42h300M12 153h300M162 20v155M12 97h300M32 70h260M32 123h260"/><path className="court-line" d="M162 3v189" stroke="#a184ce" strokeWidth="2"/><g className="court-shuttle" transform="translate(245 46) rotate(30)" stroke="#8b5cc4" strokeWidth="2"><path d="M0 0 10 24 20 0M4-2l6 26M10-4v28M16-2l-6 26M7 24q3 7 6 0"/></g><circle className="court-shuttle" cx="82" cy="121" r="5" fill="#c4a8ec" stroke="none"/></g></svg></div>;
}
const navigation = [
  ['library','/library','视频空间',Film],['analysis','/insights/match-breakdown','比赛复盘',ChartNoAxesCombined],
  ['tracker','/tracker','训练项目',FolderOpen],['cutter','/tools/video-cutter','回合剪辑',Scissors],['feedback','/feedback','反馈与建议',MessageSquarePlus],['help','/help','使用文档',BookOpen]
];
function Header({user}) {
  const [mobile,setMobile] = useState(false),[menu,setMenu]=useState(false);
  const menuRef = useRef(null);
  useEffect(()=>{const close = e=>{if(menuRef.current&&!menuRef.current.contains(e.target))setMenu(false);};document.addEventListener('pointerdown',close);return()=>document.removeEventListener('pointerdown',close);},[]);
  useEffect(()=>{const close=e=>{if(e.key==='Escape'){setMenu(false);setMobile(false);}};document.addEventListener('keydown',close);return()=>document.removeEventListener('keydown',close);},[]);
  const link = ([key,url,title,Icon])=><a key={key} href={path(url)} aria-current={view===key?'page':undefined} className={`nav-link ${view===key?'is-active':''}`}><Icon size={16} strokeWidth={1.7}/>{title}{view===key&&<motion.span layoutId="nav-active" className="nav-active"/>}</a>;
  return <header className="react-header"><div className="mx-auto flex min-h-20 max-w-[1440px] items-center gap-10 px-5 lg:px-9"><Mark/><nav aria-label="主导航" className="hidden flex-1 items-center gap-1 lg:flex"><LayoutGroup>{navigation.map(link)}</LayoutGroup></nav><div className="ml-auto flex items-center gap-3"><a className="hidden items-center gap-2 text-xs text-muted no-underline md:flex" href={path('/account/tokens')}><span className="size-1.5 rounded-full bg-violet-400"/>分析额度</a><div ref={menuRef} className="relative"><button onClick={()=>setMenu(!menu)} className="account-trigger" aria-expanded={menu} aria-label="打开账号菜单"><span className="flex size-9 items-center justify-center rounded-full bg-violet-100 font-medium text-violet-600">{user?.email?.[0]?.toUpperCase() || '羽'}</span><ChevronDown size={13} className="text-muted"/></button><AnimatePresence>{menu&&<motion.div className="account-dropdown" initial={{opacity:0,y:5}} animate={{opacity:1,y:0}} exit={{opacity:0,y:5}}><p className="break-all border-b border-line px-4 py-3 text-xs text-muted">{user?.email}</p><a href={path('/account')}>账号与管理</a><a href={path('/account/tokens')}>分析额度</a><a href={path('/login?new=1')} target="_blank" rel="noopener">登录其他账号 ↗</a><button onClick={async()=>{await api('/api/auth/logout',{method:'POST'});go('/login');}}><LogOut size={14}/>退出此账号</button></motion.div>}</AnimatePresence></div><button onClick={()=>setMobile(!mobile)} className="menu-trigger lg:hidden" aria-label="展开主导航" aria-expanded={mobile}>{mobile?<X size={20}/>:<Menu size={20}/>}</button></div></div><AnimatePresence>{mobile&&<motion.nav aria-label="手机导航" initial={{height:0,opacity:0}} animate={{height:'auto',opacity:1}} exit={{height:0,opacity:0}} className="overflow-hidden border-t border-line bg-white px-5 py-3 lg:hidden">{navigation.map(link)}<a className="nav-link" href={path('/account')}>账号管理</a></motion.nav>}</AnimatePresence></header>;
}
function Login() {
  const [mode,setMode]=useState('login'),[email,setEmail]=useState(''),[password,setPassword]=useState(''),[token,setToken]=useState(''),[confirm,setConfirm]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[registrationOpen,setRegistrationOpen]=useState(false);
  useEffect(()=>{const controller=new AbortController();api('/api/auth/status',{signal:controller.signal}).then(data=>{setRegistrationOpen(data.registration_open===true);if(data.initialized===false)setMode('setup');}).catch(e=>{if(e.name!=='AbortError')setMessage('无法读取注册状态，已有账号仍可登录。');});return()=>controller.abort();},[]);
  const changeMode=next=>{setMode(next);setMessage('');setPassword('');setConfirm('');};
  const submit=async e=>{
    e.preventDefault();if(busy)return;
    if(mode!=='login'&&password!==confirm){setMessage('两次输入的密码不一致');return;}
    setBusy(true);setMessage('');
    try {
      const route=mode==='reset'?'password-reset/complete':mode==='register'?'register':mode==='setup'?'setup':'login';
      await api('/api/auth/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-GGAB-Local':'1'},body:JSON.stringify(mode==='reset'?{token,password}:{email,password})});
      setPassword('');setConfirm('');
      if(mode==='reset'){setMode('login');setMessage('密码已更新，请登录');}else{go('/library');}
    }catch(error){setMessage(error.message);}finally{setBusy(false);}
  };
  return <main className="grid min-h-dvh place-items-center bg-canvas p-4 sm:p-8"><div className="login-frame">
    <section className="login-welcome"><Mark large/><div className="relative z-10 mb-auto mt-16 sm:mt-20"><p className="mb-5 text-[11px] tracking-[.2em] text-[#a08bbb]">给热爱，留一点时间</p><h1 className="text-[38px] font-medium leading-[1.55] tracking-tight text-[#50336b] sm:text-[46px]">从这一拍，<br/>到更好的自己。</h1><p className="mt-5 text-[13px] leading-7 text-[#9984b2]">收藏比赛的每个瞬间。<br/>回看、发现、练习，让进步看得见。</p></div><CourtArt welcome/><p className="relative z-10 mt-16 text-[10px] tracking-[.18em] text-[#b39cc9]">PLAY · REVIEW · GROW</p></section>
    <section className="login-form"><p className="mb-3 text-[10px] tracking-[.18em] text-violet-400">YOUR TRAINING SPACE</p>
      <h2 className="text-[29px] font-semibold tracking-tight text-ink">{mode==='login'?'欢迎回来。':mode==='register'?'创建你的账号。':mode==='setup'?'初始化管理员。':'设置新的密码。'}</h2>
      <p className="mb-8 mt-3 text-xs leading-6 text-muted">{mode==='login'?'登录你的账号，继续上一次的进步。':mode==='register'?'自行注册，无需激活码或管理员审批。账号默认由本站管理员管理。':mode==='setup'?'首次使用，请在本机设置管理员邮箱与独立密码。':'使用管理员提供的激活码或重置码，设置你的密码。'}</p>
      {mode!=='setup'&&<div className="mb-6 flex gap-1 rounded-xl bg-canvas p-1">
        <button type="button" disabled={busy} className={'login-tab '+(mode==='login'?'selected':'')} onClick={()=>changeMode('login')}>登录账号</button>
        {registrationOpen&&<button type="button" disabled={busy} className={'login-tab '+(mode==='register'?'selected':'')} onClick={()=>changeMode('register')}>注册账号</button>}
        <button type="button" disabled={busy} className={'login-tab '+(mode==='reset'?'selected':'')} onClick={()=>changeMode('reset')}>激活 / 重置密码</button>
      </div>}
      <form onSubmit={submit} className="flex flex-col gap-3">
        {mode!=='reset'?<label className="field-label">账号邮箱<input name="email" type="email" value={email} onChange={e=>setEmail(e.target.value)} autoComplete="username" required maxLength={254} disabled={busy}/></label>:<label className="field-label">一次性激活码<input name="token" value={token} onChange={e=>setToken(e.target.value)} required maxLength={200} autoComplete="off" disabled={busy}/></label>}
        <label className="field-label">密码<input name="password" type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete={mode==='login'?'current-password':'new-password'} minLength={12} maxLength={128} required disabled={busy}/></label>
        {mode!=='login'&&<label className="field-label">确认密码<input name="confirm-password" type="password" value={confirm} onChange={e=>setConfirm(e.target.value)} autoComplete="new-password" minLength={12} maxLength={128} required disabled={busy}/></label>}
        {mode==='register'&&<p className="text-xs leading-6 text-muted">注册的是普通账号。管理员可管理你的比赛、视频和分析额度；初始额度为 0。邮箱作为登录名，当前不发送验证邮件，请使用自己的邮箱与独立密码。</p>}
        <motion.button whileTap={{scale:.985}} disabled={busy} className="purple-button mt-3 h-12 justify-center" type="submit">{busy?<LoaderCircle size={17} className="animate-spin"/>:null}{mode==='login'?'进入羽序':mode==='register'?'注册并登录':mode==='setup'?'创建管理员':'设置密码'}<ArrowRight size={16}/></motion.button>
      </form>
      <p role="status" className="mt-3 min-h-5 text-xs text-amber-700">{message}</p><p className="mt-5 flex items-center gap-2 text-[10px] text-[#a89ab8]"><ShieldCheck size={13}/>数据与分析额度按账号独立管理</p>
    </section></div></main>;
}
function LegacyPage() {
  const [error,setError]=useState('');
  const content=useRef(document.getElementById('legacy-content')?.innerHTML || '');
  useEffect(()=>{
    (async()=>{try{for(const src of JSON.parse(host.dataset.scripts))await loadScript(src);}catch(e){setError(e.message);}})();
  },[]);
  // Only our checked-in Jinja templates enter this boundary. User input is never injected here.
  return <div className="legacy-container">{error&&<p role="alert" className="p-5 text-rose-600">{error}</p>}<div hidden aria-hidden="true"><div id="sidebar"/><button id="nav-toggle"/><button id="tokens-nav-toggle"/><button id="help-nav"/><strong id="library-count"/><span id="library-detail"/></div><div dangerouslySetInnerHTML={{__html:content.current}}/></div>;
}
function App() {
  const [user,setUser]=useState(null);
  useEffect(()=>{if(view!=='login')window.GGABAuthReady.then(setUser).catch(()=>{});},[]);
  return <MotionConfig reducedMotion="user">{view==='login'?<Login/>:<><Header user={user}/>{view==='library'?<Library CourtArt={CourtArt}/>:view==='analysis'?<Analysis/>:view==='feedback'?<Feedback/>:view==='help'?<Help/>:<LegacyPage/>}</>}</MotionConfig>;
}
// Legacy controllers bind once to real DOM nodes; StrictMode's remount is intentionally excluded.
createRoot(host).render(<App/>);
