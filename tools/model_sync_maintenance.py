"""Explicit retirement of old skins while preserving the new public collection."""
import os
import time
from urllib.parse import urlencode
from model_sync_ptero import PteroClient, SyncError


def retire_legacy(client, *, include_public=False, timeout_s=120, sleep=time.sleep, clock=time.monotonic):
    filename='all_skin_cleanup.json' if include_public else 'legacy_cleanup.json'
    route='files/list?' + urlencode({'directory':'/scriptfiles/model_sync'})
    def report_exists():
        return any(r['attributes']['name']==filename for r in client.api(route)['data'])
    if report_exists():
        client.api('files/delete',method='POST',payload={'root':'/scriptfiles/model_sync','files':[filename]})
    client.command('modelsync retireallskins' if include_public else 'modelsync retirelegacy')
    deadline=clock()+timeout_s
    while clock()<deadline:
        if report_exists():
            report=client.api('files/contents?'+urlencode({'file':'/scriptfiles/model_sync/'+filename}))
            if report.get('complete') is True:
                if (report.get('success') is not True or type(report.get('retired')) is not int
                        or report['retired']<0 or report.get('failed')!=0
                        or (include_public and report.get('scope')!='all')):
                    raise SyncError('Legacy cleanup did not complete successfully; inspect server console and backup table')
                return report
        sleep(1)
    raise SyncError('Timed out waiting for legacy cleanup; verify the updated gamemode is running')


def restart_server(client, *, timeout_s=180, sleep=time.sleep, clock=time.monotonic):
    before=client.api('resources')['attributes'].get('resources',{}).get('uptime')
    client.api('power',method='POST',payload={'signal':'restart'})
    deadline=clock()+timeout_s
    observed=False
    stable=None
    while clock()<deadline:
        sleep(5)
        state=client.api('resources')['attributes']
        running=state.get('current_state')=='running'
        uptime=state.get('resources',{}).get('uptime')
        observed=observed or not running or (type(before) is int and type(uptime) is int and uptime<before)
        if observed and running:
            if stable is None:stable=clock()
            if clock()-stable>=20:return
        else:stable=None
    raise SyncError('Server restart was not confirmed; imports must remain blocked')


def main():
    try:
        client=PteroClient(os.environ.get('PTERO_PANEL_URL',''),os.environ.get('PTERO_SERVER_ID',''),
                           os.environ.get('PTERO_CLIENT_API_KEY',''),
                           allow_insecure_http=os.environ.get('PTERO_ALLOW_INSECURE_HTTP')=='true')
        include_public=os.environ.get('MODEL_SYNC_RETIRE_ALL')=='true'
        report=retire_legacy(client,include_public=include_public)
        message=f"Retired {report['retired']} legacy skins; failed 0. New public skins preserved. Original records: legacy_skin_backup."
        if include_public:
            message=f"Retired {report['retired']} skins including the public collection; failed 0. Original records: legacy_skin_backup. Restart the server before re-importing."
        if include_public and os.environ.get('MODEL_SYNC_RESTART')=='true':
            restart_server(client)
            message+=' Server restart confirmed.'
        print(message)
        if path:=os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(path,'a') as stream:stream.write(message+'\n')
    except (SyncError,OSError,ValueError,KeyError) as exc:
        raise SystemExit(str(exc) if isinstance(exc,SyncError) else 'Legacy cleanup failed; inspect server configuration.') from None


if __name__=='__main__':main()
