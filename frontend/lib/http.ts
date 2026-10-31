/** Same-origin API requests only; CSRF token never substitutes for the HttpOnly session. */
export async function apiFetch(input:RequestInfo|URL,init?:RequestInit):Promise<Response>{
 const headers=new Headers(init?.headers);const method=(init?.method||'GET').toUpperCase();
 if(!['GET','HEAD','OPTIONS'].includes(method)&&typeof document!=='undefined'){
  const token=document.cookie.split('; ').find(value=>value.startsWith('resolve_csrf='))?.slice('resolve_csrf='.length);
  if(token)headers.set('X-CSRF-Token',decodeURIComponent(token));
 }
 const response=await globalThis.fetch(input,{...init,headers,credentials:'same-origin',cache:'no-store'});
 if(response.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event('resolve-session-expired'));
 return response;
}
