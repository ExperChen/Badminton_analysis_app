"use strict";
(() => {
  const taxonomy = {
    shot_family: {
      Serve:'发球',Clear:'高远球',Drop:'吊球',Smash:'杀球',Drive:'平抽球',Lift:'挑球',Net:'网前球',Block:'挡球',Kill:'扑球',Brush:'抹扑',Push:'推球'
    },
    shot_variant: {
      Short:'发短球',Long:'发长球','Half Smash':'半杀（减力杀球）','Cut Slice':'切吊球','Reverse Slice':'反切吊球','Flat Push':'平挑球（平推）','High Lift':'高挑球','Spin Net':'搓球',Slice:'切球'
    },
    direction: {Straight:'直线',Cross:'斜线',Middle:'中路'},
    striking_side: {'Around-the-Head':'头顶',Forehand:'正手',Backhand:'反手'},
    serve_placement: {Body:'追身',Middle:'中路',Wide:'外角'}
  };
  // Labels and stable codes share the display vocabulary; API values stay intact.
  for (const vocabulary of Object.values(taxonomy)) {
    for (const [name, label] of Object.entries(vocabulary)) {
      vocabulary[name.toLowerCase().replace(/[ -]+/g,'_')] = label;
    }
  }
  const terms = {
    ...Object.assign({}, ...Object.values(taxonomy)),
    'Shot Family':'击球类型','Shot Variation':'击球变体','Shot Variant':'击球变体',Direction:'击球线路',
    shot_family:'击球类型',shot_variant:'击球变体',shot_variation:'击球变体',direction:'击球线路',
    above_tape:'网带以上',above_net_midline:'球网中线以上',below_net_midline:'球网中线以下',below_net:'球网以下',
    into_the_net:'下网',out_the_back:'底线出界',out_on_sides:'边线出界',unknown:'未标注',winner:'制胜一击',forced_error:'受迫失误',unforced_error:'非受迫失误',in_play:'',
    'My winner':'本方制胜一击','Forced opp. error':'迫使对方失误','Drew opp. unforced':'对方非受迫失误',
    'Opp. winner':'对方制胜一击','My unforced error':'本方非受迫失误','My forced error':'本方受迫失误',
    'Long Serve':'发长球','Short Serve':'发短球','Unknown':'未标注'
  };
  const term = (value, field) => {
    if (value == null || String(value).trim() === '') return '未标注';
    const vocabulary = taxonomy[field];
    if (vocabulary && Object.hasOwn(vocabulary,value)) return vocabulary[value];
    return Object.hasOwn(terms,value) ? terms[value] : String(value);
  };
  // Backend combinations contain both the variation and its family. Translate
  // that complete phrase once so "Spin Net Net" is displayed as "搓球".
  const phrases = {
    ...terms,
    'Half Smash Smash':taxonomy.shot_variant['Half Smash'],
    'Cut Slice Drop':taxonomy.shot_variant['Cut Slice'],
    'Reverse Slice Drop':taxonomy.shot_variant['Reverse Slice'],
    'Flat Push Lift':taxonomy.shot_variant['Flat Push'],
    'High Lift Lift':taxonomy.shot_variant['High Lift'],
    'Spin Net Net':taxonomy.shot_variant['Spin Net']
  };
  const alternatives = values => [...new Set(values)].sort((a,b)=>b.length-a.length).map(v=>v.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|');
  const phrasePattern = new RegExp('\\b(?:'+alternatives(Object.keys(phrases))+')\\b','g');
  const techniquePattern = '(?:'+alternatives([...Object.values(taxonomy.shot_variant),...Object.values(taxonomy.shot_family)])+')';
  const placementPattern = '(?:'+alternatives([...Object.values(taxonomy.direction),...Object.values(taxonomy.serve_placement)])+')';
  // Keep player and striking side first, then show the route before technique.
  const shotOrderPattern = new RegExp('(^|\\s)('+techniquePattern+'(?:\\s+'+techniquePattern+')*)\\s+('+placementPattern+'(?:\\s+'+placementPattern+')*)(?=$|\\s|[，。；,.;→])','g');
  const sequence = value => String(value??'').replace(phrasePattern, match=>phrases[match])
    .replace(shotOrderPattern, (_,space,technique,placement)=>`${space}${placement} ${technique}`);
  const error = (value,diagnostic=false) => {
    const text=String(value??'操作未完成');
    if(!(diagnostic&&window.YuxuUser?.role==='admin')&&/Traceback|SHA-256|\.py\b|https?:\/\/|[A-Z]:\\|extension|cookie|access.token|ffmpeg/i.test(text))return '操作暂未完成，请稍后重试或联系管理员。';
    if(/Failed to fetch|NetworkError|Load failed|fetch failed|offline/i.test(text))return '网络连接暂时不可用，请稍后重试。';
    return text.replace(/FFmpeg/g,'剪辑服务').replace(/Session|session/g,'视频记录');
  };
  window.AppText={term,sequence,error};
})();
