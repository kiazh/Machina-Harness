import assert from "node:assert/strict";
import { test } from "node:test";
import { isKiroQuotaError } from "../src/app.ts";

test("Kiro usage/quota failures are recognized", () => {
	assert.equal(
		isKiroQuotaError(
			"agent turn failed: Kiro usage limit reached (HTTP 402): Monthly request limit exceeded.",
		),
		true,
	);
	assert.equal(isKiroQuotaError("Kiro quota exhausted"), true);
	assert.equal(isKiroQuotaError("Kiro response HTTP 429"), true);
	assert.equal(
		isKiroQuotaError(
			"provider stream error for auto: Monthly request limit exceeded. Account has reached its monthly quota.",
		),
		true,
	);
});

test("unrelated provider errors never trigger the Kiro fallback", () => {
	assert.equal(isKiroQuotaError("OpenRouter quota exhausted"), false);
	assert.equal(isKiroQuotaError("Kiro provider HTTP 500"), false);
	assert.equal(isKiroQuotaError("network timeout"), false);
});
