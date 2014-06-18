"""
"""
import os
from tempfile import tempdir

from pulsar.manager_factory import build_managers
from pulsar.cache import Cache
from pulsar.tools import ToolBox
from pulsar.tools.authorization import get_authorizer
from pulsar import messaging
from galaxy.objectstore import build_object_store_from_config
from galaxy.tools.deps import DependencyManager
from galaxy.jobs.metrics import JobMetrics
from galaxy.util.bunch import Bunch

from logging import getLogger
log = getLogger(__name__)

DEFAULT_PRIVATE_KEY = None
DEFAULT_STAGING_DIRECTORY = "lwr_staging"
DEFAULT_PERSISTENCE_DIRECTORY = "persisted_data"


NOT_WHITELIST_WARNING = "Starting the LWR without a toolbox to white-list." + \
                        "Ensure this application is protected by firewall or a configured private token."


class LwrApp(object):

    def __init__(self, **conf):
        if conf is None:
            conf = {}
        self.__setup_staging_directory(conf.get("staging_directory", DEFAULT_STAGING_DIRECTORY))
        self.__setup_private_key(conf.get("private_key", DEFAULT_PRIVATE_KEY))
        self.__setup_persistence_directory(conf.get("persistence_directory", None))
        self.__setup_tool_config(conf)
        self.__setup_object_store(conf)
        self.__setup_dependency_manager(conf)
        self.__setup_job_metrics(conf)
        self.__setup_managers(conf)
        self.__setup_file_cache(conf)
        self.__setup_bind_to_message_queue(conf)

    def shutdown(self):
        for manager in self.managers.values():
            try:
                manager.shutdown()
            except Exception:
                pass

        if self.__queue_state:
            self.__queue_state.deactivate()

    def __setup_bind_to_message_queue(self, conf):
        message_queue_url = conf.get("message_queue_url", None)
        queue_state = None
        if message_queue_url:
            queue_state = messaging.bind_app(self, message_queue_url, conf)
        self.__queue_state = queue_state

    def __setup_tool_config(self, conf):
        """
        Setups toolbox object and authorization mechanism based
        on supplied toolbox_path.
        """
        tool_config_files = conf.get("tool_config_files", None)
        if not tool_config_files:
            # For compatibity with Galaxy, allow tool_config_file
            # option name.
            tool_config_files = conf.get("tool_config_file", None)
        toolbox = None
        if tool_config_files:
            toolbox = ToolBox(tool_config_files)
        else:
            log.info(NOT_WHITELIST_WARNING)
        self.toolbox = toolbox
        self.authorizer = get_authorizer(toolbox)

    def __setup_staging_directory(self, staging_directory):
        self.staging_directory = os.path.abspath(staging_directory)

    def __setup_managers(self, conf):
        self.managers = build_managers(self, conf)

    def __setup_private_key(self, private_key):
        self.private_key = private_key
        if private_key:
            log.info("Securing LWR web app with private key, please verify you are using HTTPS so key cannot be obtained by monitoring traffic.")

    def __setup_persistence_directory(self, persistence_directory):
        self.persistence_directory = persistence_directory or DEFAULT_PERSISTENCE_DIRECTORY

    def __setup_file_cache(self, conf):
        file_cache_dir = conf.get('file_cache_dir', None)
        self.file_cache = Cache(file_cache_dir) if file_cache_dir else None

    def __setup_object_store(self, conf):
        if "object_store_config_file" not in conf:
            self.object_store = None
            return
        object_store_config = Bunch(
            object_store_config_file=conf['object_store_config_file'],
            file_path=conf.get("object_store_file_path", None),
            object_store_check_old_style=False,
            job_working_directory=conf.get("object_store_job_working_directory", None),
            new_file_path=conf.get("object_store_new_file_path", tempdir),
            umask=int(conf.get("object_store_umask", "0000")),
        )
        self.object_store = build_object_store_from_config(object_store_config)

    def __setup_dependency_manager(self, conf):
        dependencies_dir = conf.get("tool_dependency_dir", "dependencies")
        resolvers_config_file = conf.get("dependency_resolvers_config_file", "dependency_resolvers_conf.xml")
        self.dependency_manager = DependencyManager(dependencies_dir, resolvers_config_file)

    def __setup_job_metrics(self, conf):
        job_metrics_config_file = conf.get("job_metrics_config_file", "job_metrics_conf.xml")
        self.job_metrics = JobMetrics(job_metrics_config_file)
