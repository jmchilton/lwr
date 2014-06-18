import os
import json
import urllib
import time


def test_standard_requests():
    """ Tests app controller methods. These tests should be
    compartmentalized. Also these methods should be made to not retest
    the behavior of the associated Manager class. """
    from .test_utils import test_pulsar_app

    with test_pulsar_app(test_conf={"extra_environ": {"REMOTE_ADDR": "127.101.101.98"}}) as app:
        staging_directory = app.app.staging_directory
        setup_response = app.post("/jobs?job_id=12345")
        setup_config = json.loads(setup_response.body)
        assert setup_config["working_directory"].startswith(staging_directory)
        outputs_directory = setup_config["outputs_directory"]
        assert outputs_directory.startswith(staging_directory)
        assert setup_config["path_separator"] == os.sep
        job_id = setup_config["job_id"]

        def test_upload(upload_type):
            url = "/jobs/%s/files?name=input1&type=%s" % (job_id, upload_type)
            upload_input_response = app.post(url, "Test Contents")
            upload_input_config = json.loads(upload_input_response.body)
            staged_input_path = upload_input_config["path"]
            staged_input = open(staged_input_path, "r")
            try:
                assert staged_input.read() == "Test Contents"
            finally:
                staged_input.close()
        test_upload("input")
        test_upload("tool")

        test_output = open(os.path.join(outputs_directory, "test_output"), "w")
        try:
            test_output.write("Hello World!")
        finally:
            test_output.close()
        download_response = app.get("/jobs/%s/files?name=test_output&type=output" % job_id)
        assert download_response.body == "Hello World!"

        try:
            app.get("/jobs/%s/files?name=test_output2&type=output" % job_id)
            assert False  # Should throw exception
        except:
            pass

        command_line = urllib.quote("""python -c "import sys; sys.stdout.write('test_out')" """)
        launch_response = app.post("/jobs/%s/submit?command_line=%s" % (job_id, command_line))
        assert launch_response.body == 'OK'

        # Hack: Call twice to ensure postprocessing occurs and has time to
        # complete. Monitor thread should get this.
        time.sleep(.2)
        check_response = app.get("/jobs/%s/status" % job_id)
        time.sleep(.2)
        check_response = app.get("/jobs/%s/status" % job_id)
        check_config = json.loads(check_response.body)
        assert check_config['returncode'] == 0
        assert check_config['stdout'] == "test_out"
        assert check_config['stderr'] == ""

        kill_response = app.put("/jobs/%s/cancel" % job_id)
        assert kill_response.body == 'OK'

        clean_response = app.delete("/jobs/%s" % job_id)
        assert clean_response.body == 'OK'
        assert os.listdir(staging_directory) == []
