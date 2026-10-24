// The port the *generated* .NET service listens on when someone runs an exported bundle on their
// own machine. Not this platform's API port, which lives in scripts/root-env.ts and is resolved
// from the repo-root .env: these are two different processes and two different ports, and the one
// here is baked into artifacts the engine generates (the Dockerfile's EXPOSE, the compose file's
// port mapping, run-local.sh and RUN_LOCALLY.md), so it is fixed by the bundle, not by this app's
// configuration.
//
// It has one home here for the same reason the API port has one: so a change in the infrastructure
// generator cannot leave the Export panel telling people to open a port nothing is listening on.
// api/tests/test_dev_ports.py holds this value and the generator's TARGET_PORT to each other.
export const GENERATED_SERVICE_PORT = 8080;

export const GENERATED_SERVICE_URL = `http://localhost:${GENERATED_SERVICE_PORT}`;

export const GENERATED_SWAGGER_URL = `${GENERATED_SERVICE_URL}/swagger`;
