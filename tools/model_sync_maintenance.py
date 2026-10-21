"""Explicit retirement of old skins while preserving the new public collection."""
import os
import time
from urllib.parse import urlencode
from model_sync_ptero import PteroClient, SyncError


def retire_legacy(client, *, timeout_s=120, sleep=time.sleep, clock=time.monotonic):
    route='files/list?' + urlencode({'directory':'/scriptfiles/model_sync'})
    def report_exists():
        return any(r['attributes']['name']=='legacy_cleanup.json' for r in client.api(route)['data'])
    if report_exists():
        client.api('files/delete',method='POST',payload={'root':'/scriptfiles/model_sync','files':['legacy_cleanup.json']})
    client.command('modelsync retirelegacy')
    deadline=clock()+timeout_s
    while clock()<deadline:
        if report_exists():
            report=client.api('files/contents?'+urlencode({'file':'/scriptfiles/model_sync/legacy_cleanup.json'}))
            if report.get('complete') is True:
                if (report.get('success') is not True or type(report.get('retired')) is not int
                        or report['retired']<0 or report.get('failed')!=0):
                    raise SyncError('Legacy cleanup did not complete successfully; inspect server console and backup table')
                return report
        sleep(1)
    raise SyncError('Timed out waiting for legacy cleanup; verify the updated gamemode is running')


def main():
    try:
        client=PteroClient(os.environ.get('PTERO_PANEL_URL',''),os.environ.get('PTERO_SERVER_ID',''),
                           os.environ.get('PTERO_CLIENT_API_KEY',''),
                           allow_insecure_http=os.environ.get('PTERO_ALLOW_INSECURE_HTTP')=='true')
        report=retire_legacy(client)
        message=f"Retired {report['retired']} legacy skins; failed 0. New public skins preserved. Original records: legacy_skin_backup."
        print(message)
        if path:=os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(path,'a') as stream:stream.write(message+'\n')
    except (SyncError,OSError,ValueError,KeyError) as exc:
        raise SystemExit(str(exc) if isinstance(exc,SyncError) else 'Legacy cleanup failed; inspect server configuration.') from None


if __name__=='__main__':main()
