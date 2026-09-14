const byId=id=>document.getElementById(id);
let sessionId=null,lastIntent=null,lastRequest=null;
async function api(path,body){
  const response=await fetch(path,{method:body?'POST':'GET',headers:{'Authorization':`Bearer ${byId('access').value}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  const data=await response.json();
  if(!response.ok)throw new Error(data.detail?.message||data.detail?.code||'请求失败');
  return data;
}
async function perform(fn){byId('error').textContent='';byId('send').disabled=true;try{await fn();}catch(error){byId('error').textContent=error.message;}finally{byId('send').disabled=false;}}
byId('create').onclick=()=>perform(async()=>{const result=await api('/api/intent/sessions',{title:'意图对话'});sessionId=result.session_id;lastIntent=null;lastRequest=null;byId('session').textContent='对话已创建';byId('messages').replaceChildren();});
async function showResult(result){
  lastIntent=result.intent;const article=document.createElement('article');article.textContent=result.content;
  if(result.receipt){const detail=document.createElement('small');detail.textContent=`\n${result.receipt.status==='accepted'?'请求已接收，尚未完成':'处理结果已保存'}`;article.append(detail);
    const artifact=await api(`/api/intent/sessions/${sessionId}/artifacts/${encodeURIComponent(result.receipt.downstream_id)}`);
    const pre=document.createElement('pre');pre.textContent=JSON.stringify(artifact,null,2);article.append(pre);}
  byId('messages').append(article);
}
byId('send').onclick=()=>perform(async()=>{
  if(!sessionId)throw new Error('请先新建对话');const content=byId('message').value.trim();if(!content)throw new Error('请输入消息');
  const body={message_id:crypto.randomUUID(),content};
  if(byId('relation').value==='follow'){if(!lastIntent)throw new Error('没有可补充的请求');body.intent_id=lastIntent.identity.intent_id;body.expected_revision=lastIntent.identity.revision;}
  lastRequest=body;const result=await api(`/api/intent/sessions/${sessionId}/messages`,body);await showResult(result);byId('message').value='';
});
byId('refresh').onclick=()=>perform(async()=>{if(!lastRequest)throw new Error('暂无请求');await showResult(await api(`/api/intent/sessions/${sessionId}/messages`,lastRequest));});
