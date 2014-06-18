from pulsar.mesos import (
    ensure_mesos_libs
)
from pulsar.mesos.framework import run

from pulsar.daemon import (
    ArgumentParser,
    PulsarManagerConfigBuilder,
)

DESCRIPTION = "Pulsar Mesos Framework Entry Point."


def main():
    ensure_mesos_libs()
    arg_parser = ArgumentParser(
        description=DESCRIPTION,
    )
    arg_parser.add_argument("--master", default=None, required=True)
    PulsarManagerConfigBuilder.populate_options(arg_parser)
    args = arg_parser.parse_args()

    config_builder = PulsarManagerConfigBuilder(args)
    config_builder.setup_logging()
    config = config_builder.load()

    run(
        master=args.master,
        manager_options=config_builder.to_dict(),
        config=config
    )


if __name__ == "__main__":
    main()
