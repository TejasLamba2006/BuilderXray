import RunClient from './run-client';
export function generateStaticParams(){return [{jobId:'demo'}]}
export default function RunPage({params}:{params:Promise<{jobId:string}>}){return <RunClient params={params}/>}
