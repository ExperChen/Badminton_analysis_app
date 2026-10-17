import React,{useEffect,useId,useRef} from 'react';
import {motion} from 'motion/react';
import {X} from 'lucide-react';

export default function Modal({title,subtitle,children,actions,onClose,wide=false,className=''}) {
  const ref=useRef(null),titleId=useId();
  useEffect(()=>{
    const dialog=ref.current,previousOverflow=document.body.style.overflow;
    document.body.style.overflow='hidden';dialog.showModal();
    return()=>{dialog.close();document.body.style.overflow=previousOverflow;};
  },[]);
  return <motion.dialog ref={ref} aria-labelledby={titleId} className={`yu-modal ${wide?'yu-modal-wide':''} ${className}`} initial={{opacity:0,y:12,scale:.98}} animate={{opacity:1,y:0,scale:1}} exit={{opacity:0,y:8,scale:.98}} transition={{duration:.18}}
    onCancel={event=>{event.preventDefault();onClose();}} onClick={event=>{if(event.target===ref.current){const r=ref.current.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)onClose();}}}>
    <header className="yu-modal-header"><div>{subtitle&&<p className="yu-kicker">{subtitle}</p>}<h2 id={titleId}>{title}</h2></div><button type="button" aria-label="关闭弹窗" className="icon-button" onClick={onClose}><X size={19}/></button></header>
    <div className="yu-modal-body">{children}</div>{actions&&<footer className="yu-modal-actions">{actions}</footer>}
  </motion.dialog>;
}
