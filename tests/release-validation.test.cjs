const { test } = require("node:test");
const assert = require("node:assert/strict");
const validateRelease = require("../.github/scripts/validate-release.cjs");

function fixture({ runs, jobs, ref = "refs/heads/main" } = {}) {
  const run = {
    id: 42, run_number: 7, head_sha: "candidate", head_branch: "main",
    event: "push", status: "completed", conclusion: "success",
    html_url: "https://github.com/example/project/actions/runs/42",
  };
  const successJobs = [
    "hassfest", "HACS validation", "pytest", "Repository checks",
    "Home Assistant 2026.9.4 runtime", "Home Assistant 2026.10.4 runtime",
  ].map((name) => ({ name, status: "completed", conclusion: "success" }));
  const calls = [];
  const args = {
    context: { ref, sha: "candidate", repo: { owner: "example", repo: "project" } },
    core: { info: (message) => calls.push(message) },
    github: {
      rest: { actions: {
        listWorkflowRuns: async (request) => {
          assert.equal(request.head_sha, "candidate");
          assert.equal(request.event, "push");
          assert.equal(request.workflow_id, "validate.yml");
          return { data: { workflow_runs: runs === undefined ? [run] : runs } };
        },
        listJobsForWorkflowRun: () => {},
      } },
      paginate: async (_method, request) => {
        assert.equal(request.run_id, 42);
        assert.equal(request.filter, "latest");
        return jobs === undefined ? successJobs : jobs;
      },
    },
  };
  return { args, run, successJobs, calls };
}

test("allows a completed exact-commit main push with all checks successful", async () => {
  const { args, calls } = fixture();
  await validateRelease(args);
  assert.equal(calls.length, 1);
});

test("rejects dispatch from another branch", async () => {
  await assert.rejects(validateRelease(fixture({ ref: "refs/heads/topic" }).args), /main/);
});

test("rejects no run and unrelated commit, branch, or event", async () => {
  const { run } = fixture();
  for (const runs of [
    [], [{ ...run, head_sha: "old" }], [{ ...run, head_branch: "topic" }],
    [{ ...run, event: "pull_request" }],
  ]) {
    await assert.rejects(validateRelease(fixture({ runs }).args), /exact main commit/);
  }
});

test("does not use an older success when the latest run failed or is pending", async () => {
  const { run } = fixture();
  for (const latest of [
    { status: "completed", conclusion: "failure" },
    { status: "in_progress", conclusion: null },
    { status: "completed", conclusion: "cancelled" },
  ]) {
    const runs = [run, { ...run, id: 43, run_number: 8, ...latest }];
    await assert.rejects(validateRelease(fixture({ runs }).args), /finish successfully/);
  }
});

test("rejects failed, skipped, and unfinished jobs even if run says success", async () => {
  const { successJobs } = fixture();
  for (const change of [
    { conclusion: "failure" }, { conclusion: "skipped" },
    { status: "in_progress", conclusion: null },
  ]) {
    const jobs = successJobs.map((job, i) => i === 0 ? { ...job, ...change } : job);
    await assert.rejects(validateRelease(fixture({ jobs }).args), /Validation jobs/);
  }
});

test("requires HACS, hassfest, tests, repository checks, and HA runtime coverage", async () => {
  const { successJobs } = fixture();
  for (const name of ["hassfest", "HACS validation", "pytest", "Repository checks"]) {
    await assert.rejects(
      validateRelease(fixture({ jobs: successJobs.filter((job) => job.name !== name) }).args),
      /Validation jobs/
    );
  }
  await assert.rejects(
    validateRelease(fixture({ jobs: successJobs.filter((job) => !job.name.startsWith("Home Assistant ")) }).args),
    /Validation jobs/
  );
});

test("fails closed on GitHub API errors", async () => {
  const { args } = fixture();
  args.github.rest.actions.listWorkflowRuns = async () => { throw new Error("API unavailable"); };
  await assert.rejects(validateRelease(args), /API unavailable/);
});
