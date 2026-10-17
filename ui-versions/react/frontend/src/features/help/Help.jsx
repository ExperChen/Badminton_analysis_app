import React from 'react';
import {BookOpen, ArrowRight} from 'lucide-react';
import {path} from '../../lib/api';

const chapters=[
  {id:'access',title:'访问与登录',text:'先连接管理员授权的 Tailscale 私有网络，再打开 HTTPS 网站地址。网站账号与 VPN 身份是两层不同的登录。',items:['登录页选择“注册账号”，用自己的邮箱和至少十二字符的独立密码注册，无需管理员批准或激活码。新账号默认由本站管理员管理，初始分析额度为 0。','邮箱目前只作为登录名，不发送验证邮件。管理员仍可代建账号；激活或忘记密码时使用十五分钟有效的一次性激活／重置码。','同一浏览器需要使用其他账号时，选择账号菜单中的“登录其他账号”；各窗口身份独立。','不要分享管理员的 Google、分析服务或 Tailscale 凭据。网站注册不会自动获得 VPN 访问权限。'],link:'/account',action:'账号与管理'},
  {id:'library',title:'视频空间：已分析与待分析',text:'搜索原片名称，筛选已分析或待分析。点击卡片播放原片，点击“查看复盘”在本站打开逐拍数据。',items:['“已分析”表示该比赛已有精确关联的逐拍数据，不单独证明远端片源当前可用。','“待分析”表示这张视频卡片尚未关联逐拍数据。如果别处已有同场数据，请管理员核验原片与比赛 ID，不要直接重传或付费重建。'],link:'/library',action:'打开视频空间'},
  {id:'project',title:'创建训练项目与球员截图',text:'依次选择原片，填写标题、实际比赛日期、单打或双打、本人位置、球员名称和惯用手。年份使用四位数，选定视频后再次核对日期。',items:['在双方都清晰可见的同一帧，用不同颜色的框标注球员。重新选帧或旋转会清除原有选框。','等待每位球员截图保存成功，再继续。保存过的名称按本地账号保留，其他账号看不到这些候选名称。','片段起止秒数使用完整原片时间。选择先发球方；未填写的比分不推测。片段为空表示完整视频、近端先发球。','核对所有截图、日期和设置后保存。管理员电脑上的后台扩展负责迁移，无需远程用户在真实服务中重复创建项目。'],link:'/tracker',action:'打开训练项目'},
  {id:'tokens',title:'分析额度与费用确认',text:'每个项目停在费用确认前。等待真实服务报价，再点击确认。报价改变时，需查看新金额后重新确认。',items:['没有报价、扩展断连、片源核验失败或写入结果不确定时，不要连续提交。','子账号显示的是自己可用、预留和已结算的额度，不是共享账号的总余额。可用额度为 0 时请联系管理员。','确认时预留费用，处理完成并核验真实收费记录后结算。预计费用不等于已扣款，分配本地额度不会改变远端余额。'],link:'/account/tokens',action:'查看我的分析额度'},
  {id:'replay',title:'比赛复盘与回合剪辑',text:'选择比赛、局数和观察方，点击逐拍记录的时间定位原片，可切换回合和播放倍速。',items:['普通击球的结果栏留空；终结结果显示“制胜一击”、受迫失误或非受迫失误。','观察方来自比赛项目的球员设置，不按上传账号推断。','回合剪辑是独立工具，生成衍生文件而不替换原片。首次生成流畅版本或精剪需要等待。'],link:'/insights/match-breakdown',action:'打开比赛复盘'},
  {id:'issues',title:'遇到问题时',text:'先确认页面显示的阶段，避免把上传完成、分析完成和费用结算当作同一件事。',items:['有数据但卡片待分析：管理员核验精确关联，不能只按标题合并。','本地能播放、远端片源缺失：两种片源不同，保留已完成标注，不表示远端已经修复。','视频已上传过或上传卡住：核查已有远端视频和进度，不重复上传。','截图失败：确认画面已就绪后重新绘框；持续失败时暂停。','登录失效：在对应窗口重新登录；后台真实服务登录由管理员核对。','私有地址无法打开：核对 VPN 授权、在线状态、管理员电脑和服务是否运行。']},
  {id:'privacy',title:'数据与反馈',text:'普通账号只访问本人数据，管理员可管理所有账号。网页 CSV 下载已关闭，后台仍同步真实逐拍数据。',items:['管理员电脑、网站服务、后台扩展及真实服务登录必须保持可用，远程用户不需要重复配置上传扩展。','反馈仅本人和管理员可见。描述操作步骤、当前表现与预期结果，不填写密码、激活码、令牌或其他密钥。','反馈审核、开发与生产发布是不同阶段，不保证提交后立即上线。'],link:'/feedback',action:'反馈与建议'}
];

export default function Help(){
  return <main className="analysis-main help-main"><header className="analysis-page-heading"><div><p className="yu-kicker">YOUR GUIDE TO THE NEXT SHOT</p><h1>使用文档</h1><p>从第一段视频，到每一拍的复盘。更新于 2026-10-13</p></div><BookOpen size={32} strokeWidth={1.4}/></header><nav className="help-index" aria-label="文档目录">{chapters.map(c=><a key={c.id} href={'#'+c.id}>{c.title}</a>)}</nav><div className="help-chapters">{chapters.map(c=><section id={c.id} key={c.id} className="help-card"><h2>{c.title}</h2><p>{c.text}</p><ul>{c.items.map(item=><li key={item}>{item}</li>)}</ul>{c.link&&<a className="analysis-text-button" href={path(c.link)}>{c.action}<ArrowRight size={13}/></a>}</section>)}</div><p className="analysis-note">这是用户操作文档。接口诊断文档仅管理员可访问；本页不执行上传、费用确认或数据修改。</p></main>;
}
