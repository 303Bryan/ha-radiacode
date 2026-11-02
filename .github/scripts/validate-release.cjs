// Used by actions/github-script; exercised without network or publication in tests.
module.exports = async function validateRelease({ github, context, core }) {
  if (context.ref !== "refs/heads/main") {
    throw new Error("Releases must be dispatched from main.");
  }

  const { data } = await github.rest.actions.listWorkflowRuns({
    ...context.repo,
    workflow_id: "validate.yml",
    head_sha: context.sha,
    branch: "main",
    event: "push",
    per_page: 100,
  });
  const run = data.workflow_runs
    .filter((item) =>
      item.head_sha === context.sha &&
      item.head_branch === "main" &&
      item.event === "push"
    )
    .sort((a, b) => b.run_number - a.run_number)[0];

  if (!run || run.status !== "completed" || run.conclusion !== "success") {
    throw new Error(
      "The latest Validate push run for this exact main commit must finish successfully before release."
    );
  }

  const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRun, {
    ...context.repo,
    run_id: run.id,
    filter: "latest",
    per_page: 100,
  });
  const names = new Set(jobs.map((job) => job.name));
  const required = ["hassfest", "HACS validation", "pytest", "Repository checks"];
  if (
    required.some((name) => !names.has(name)) ||
    !jobs.some((job) => job.name.startsWith("Home Assistant ") && job.name.endsWith(" runtime")) ||
    jobs.some((job) => job.status !== "completed" || job.conclusion !== "success")
  ) {
    throw new Error("Validation jobs are missing, skipped, incomplete, or unsuccessful.");
  }

  core.info("Release validation passed: " + run.html_url);
};
