try {
  const mod = await import("<repo>/ui/src/features/mode.ts");
  console.log("import ok:", Object.keys(mod).join(","));
  console.log("approvalsCommand.run type:", typeof mod.approvalsCommand?.run);
} catch (err) {
  console.log("import failed:", err.message);
  console.log(err.stack);
}
