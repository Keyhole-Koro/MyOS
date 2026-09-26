# MyOS

MyOS implements OS services and desktop policy: filesystem, UI server/DOM,
application discovery and launch, process integration, and cleanup. It is the
provider of MyStdLib's hosted APIs and the root `contracts/` consumed by both
clients and providers. MyOS implementation code imports no MyAppFramework
module.

The source tree makes each concrete boundary explicit instead of collecting
them under a generic service-adapter layer:

- `src/syscall/`: the OS_CALL registration point and user-memory copy helpers.
- `src/ipc/`: request/reply/event transport between processes and the service task.
- `src/fs/syscall.mln`, `src/proc/syscall.mln`: domain-local syscall endpoints.
- `src/ui/protocol.mln`, `src/shell/protocol.mln`: domain-local wire dispatch.
- The remaining files under `src/ui`, `src/fs`, `src/proc`, and `src/shell`
  implement domain behavior and policy without depending on those endpoints.

UI and shell code do not import service transport. They expose event-sink
ports; `syscall/router.mln` wires those ports to the process transport during
boot.

Filesystem operations use the single `FsError` definition in
`contracts/io/fs.contract.mln`. `fs/syscall.mln` copies `Result<T, FsError>` values
across the user-memory boundary without converting errors or `Option::None`
to integer sentinels. Simple operations dispatch directly from its single
`handle()` function; only operations that actually stage buffers have helpers.

Desktop app sources live in `src/apps/` for image packaging, but they are SDK
clients, not part of the MyOS implementation layer. Each is compiled with
MyAppFramework into its own `/apps/*.mbin` process and may import the
app-facing `MyAppFramework/src/*.mln` files plus MyStdLib, but not MyOS
internals. See `system/MyAppFramework/docs/APP_FRAMEWORK.md` for app authoring
and `docs/design/os-app-boundaries.md` in MyComputer for the enforced boundary.
