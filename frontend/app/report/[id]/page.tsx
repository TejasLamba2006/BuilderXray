import ReportClient from './report-client';
export function generateStaticParams(){return [{id:'saved-dlf'},{id:'saved-godrej'},{id:'saved-parsvnath'},{id:'saved-skyline'}]}
export default function ReportPage({params}:{params:Promise<{id:string}>}){return <ReportClient params={params}/>}
