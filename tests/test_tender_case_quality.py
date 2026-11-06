import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tender_case_quality import merge_oa_logs, group_process_links, image_coverage, file_namespace, valid_image


def test_process_sources_do_not_duplicate_instance():
    assert group_process_links([('p', '申请'), ('p', '档案')]) == [('p', '申请、档案')]


def test_old_images_participate_in_coverage():
    assert image_coverage(2, 0) == ('全部缺失', '0/2 本地')
    assert image_coverage(3, 2) == ('部分缺失', '2/3 本地')


def test_migration_numeric_size_is_not_namespace_evidence():
    assert file_namespace(True, True, False) == 'tender_infra'
    assert file_namespace(True, True, True) == 'ambiguous'
    assert file_namespace(True, False, True) == 'e9_imagefile'
    assert file_namespace(True, False, False) == 'unknown'


def test_two_log_sources_merge_only_exact_cross_source_duplicates():
    row = dict(requestid=1, nodeid=2, operator=3, operatedate='2026-11-05', operatetime='10:00:00', remark='同意', LOGID=1)
    a = dict(row, source='workflow_requestLog')
    b = dict(row, LOGID=8, source='workflow_approvelog')
    c = dict(a, LOGID=2, operatetime='10:01:00')
    result = merge_oa_logs([a, b, c])
    assert len(result) == 2
    assert len(result[0]['sources']) == 2


def test_html_error_is_not_an_image(tmp_path):
    f = tmp_path / 'error.png'; f.write_bytes(b'<html>login required</html>')
    assert not valid_image(f)


def test_actual_case_counts_missing_old_image():
    from types import SimpleNamespace
    from tender_case_export import Case
    class Resources:
        def lookup(self, url):
            return None, 'HTTP 403'
    ctx = SimpleNamespace(old_resources=Resources(), proc_links={}, users={})
    case = Case(ctx, None, {'id': 1})
    case.old_img_rewrite(2, 3, '<img src="/weaver/FileDownload?fileid=4">')
    case.sec_flow()
    assert case.cover['images_total'] == 1
    assert case.cover['image'] == '全部缺失'
    assert case.rel['images'][0]['status'] == 'HTTP 403'


def test_data_image_is_persisted_and_verified(tmp_path):
    import base64, io
    from PIL import Image
    from tender_case_quality import OldResources
    buffer = io.BytesIO()
    Image.new('RGB', (2, 2), 'pink').save(buffer, format='PNG')
    store = OldResources(tmp_path, tmp_path / 'out', 'http://example.test')
    url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
    key, result, status = store.fetch((url, True))
    assert status == 'ok'
    assert valid_image(result['path'])
    store.index[key] = result
    assert store.lookup(url)[0] == result['path']


def test_login_page_is_not_saved_as_resource(tmp_path):
    from tender_case_quality import OldResources
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args):
            if getattr(self, 'done', False): return b''
            self.done = True
            return b'<html><form id="loginform">Login</form></html>'
        def geturl(self): return 'http://example.test/login.jsp'
    class Opener:
        def open(self, *args, **kwargs): return Reply()
    store = OldResources(tmp_path, tmp_path / 'out', 'http://example.test')
    store.opener = Opener()
    _, result, status = store.fetch(('http://example.test/image', True))
    assert status == 'fail' and not result.get('path')
    assert store.auth_failed.is_set()


def test_redirect_drops_credentials_on_other_origin():
    import urllib.request
    from tender_case_quality import CookieSafeRedirect
    req = urllib.request.Request('http://oa.test/file', headers={'Cookie': 'private', 'Authorization': 'secret'})
    moved = CookieSafeRedirect().redirect_request(req, None, 302, '', {}, 'http://other.test/image')
    assert not any(k.lower() in ('cookie', 'authorization') for k in moved.headers)


def test_transient_error_cache_is_retryable(tmp_path):
    from tender_case_quality import OldResources
    store = OldResources(tmp_path, tmp_path / 'out', 'http://example.test')
    url = 'http://example.test/img'
    store.index[url] = {'error': 'timed out', 'session': store.session}
    class Opener:
        def open(self, *a, **k): raise ValueError('retried transient request')
    store.opener = Opener()
    _, result, _ = store.fetch((url, True))
    assert 'retried transient request' in result['error']


def test_mapped_requests_are_loaded_before_cache_coverage(tmp_path):
    import pickle
    from types import SimpleNamespace
    from unittest.mock import patch
    import tender_case_export as export
    cache = {'form': {(-130, 5): [2]}, 'covered_reqs': {1}, 'covered_files': set()}
    (tmp_path / 'oa.pkl').write_bytes(pickle.dumps(cache))
    ctx = SimpleNamespace(applies=[{'id': 1, 'oa_source': 1, 'oa_id': 5, 'process_instance_id': '1'}])
    env = dict(DB_HOST='test', DB_PORT='1', DB_USER='test', DB_PWD_READONLY='test')
    with patch.object(export, 'CACHE_DIR', str(tmp_path)), patch.object(export, 'collect_file_refs', return_value=[]), patch('pytds.connect', side_effect=RuntimeError('query intercepted')) as connect:
        try:
            export.load_oa(env, ctx, export.Progress(), False)
        except RuntimeError:
            pass
        assert connect.call_args.kwargs['timeout'] == 900  # requestbase/requestLog block, not approvals-only


def test_external_fileid_cannot_reuse_oa_image(tmp_path):
    from PIL import Image
    from tender_case_quality import OldResources
    folder = tmp_path / 'data/e9-opinion-export/images'
    folder.mkdir(parents=True)
    Image.new('RGB', (2, 2)).save(folder / '123.png')
    store = OldResources(tmp_path, tmp_path / 'out', 'http://oa.test')
    assert store.lookup('http://other.test/image?fileid=123')[0] is None
    assert store.lookup('http://oa.test/weaver/weaver.file.FileDownload?fileid=123')[0]


def test_markdown_oa_image_is_a_download_source():
    from tender_case_quality import image_sources
    assert image_sources('![](/weaver/weaver.file.FileDownload?fileid=3)') == ['/weaver/weaver.file.FileDownload?fileid=3']


def test_external_login_logo_is_not_oa_auth_failure(tmp_path):
    import io
    from PIL import Image
    from tender_case_quality import OldResources
    buffer = io.BytesIO(); Image.new('RGB', (2, 2)).save(buffer, format='PNG')
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, *args):
            if getattr(self, 'done', False): return b''
            self.done = True
            return buffer.getvalue()
        def geturl(self): return 'http://external.test/login-logo.png'
    class Opener:
        def open(self, *a, **k): return Reply()
    store = OldResources(tmp_path, tmp_path / 'out', 'http://oa.test')
    store.opener = Opener()
    assert store.fetch(('http://external.test/login-logo.png', True))[2] == 'ok'
    assert not store.auth_failed.is_set()


def test_wrong_sized_attachment_is_not_counted_as_valid(tmp_path):
    from tender_case_quality import OldResources
    store = OldResources(tmp_path, tmp_path / 'out', 'http://oa.test')
    f = tmp_path / 'short.bin'; f.write_bytes(b'no rights')
    url = 'http://oa.test/weaver/weaver.file.FileDownload?fileid=99'
    store.index[url] = {'path': str(f)}
    store.file_sizes[url] = 1000
    assert store.lookup(url, image=False)[0] is None


if __name__ == '__main__':
    import tempfile
    test_process_sources_do_not_duplicate_instance()
    test_old_images_participate_in_coverage()
    test_migration_numeric_size_is_not_namespace_evidence()
    test_two_log_sources_merge_only_exact_cross_source_duplicates()
    test_actual_case_counts_missing_old_image()
    test_redirect_drops_credentials_on_other_origin()
    test_markdown_oa_image_is_a_download_source()
    with tempfile.TemporaryDirectory() as tmp:
        test_html_error_is_not_an_image(Path(tmp))
        test_data_image_is_persisted_and_verified(Path(tmp))
        test_login_page_is_not_saved_as_resource(Path(tmp))
        test_transient_error_cache_is_retryable(Path(tmp))
        test_mapped_requests_are_loaded_before_cache_coverage(Path(tmp))
        test_external_fileid_cannot_reuse_oa_image(Path(tmp))
        test_external_login_logo_is_not_oa_auth_failure(Path(tmp))
        test_wrong_sized_attachment_is_not_counted_as_valid(Path(tmp))
    print('15 quality regression tests passed')
