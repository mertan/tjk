export function isWithdrawn(value) {
  return value === true || value === 1 || value === '1' || value === 'true';
}

export function raceDataIssues(feed, programRace) {
  // The observed muhtemeller.timestamp matches scheduled race time, while
  // checksum.datetime is a global heartbeat without a timezone. Neither
  // establishes quote freshness. Keep the live adapter closed until an
  // independently verified source contract supplies race-level freshness.
  const reasons = ['SOURCE_FRESHNESS_UNVERIFIED'];
  const info = feed.racePayload.data.muhtemeller;
  const rows = info.bahisler?.find((bet) => bet.B === 'GANYAN')?.muhtemeller;
  if (!Array.isArray(rows) || !rows.length) reasons.push('MARKET_UNAVAILABLE');
  if (!programRace) reasons.push('PROGRAM_UNAVAILABLE');
  else {
    if (!programRace.type || !programRace.condition || !programRace.distance || !programRace.surface) reasons.push('PROGRAM_METADATA_MISSING');
    const activeProgram = programRace.runners.filter((r) => !/\(ko[şs]maz\)/iu.test(r.rawName));
    const feedNumbers = new Set((rows || []).map((r) => Number(r.S1)));
    const programNumbers = new Set(programRace.runners.map((r) => r.number));
    if (programNumbers.size !== programRace.runners.length) reasons.push('PROGRAM_RUNNER_DUPLICATE');
    if (activeProgram.some((r) => !feedNumbers.has(r.number)) ||
        (rows || []).some((r) => !isWithdrawn(r.KOSMAZ) && !programNumbers.has(Number(r.S1)))) reasons.push('RUNNER_SET_MISMATCH');
    if ((rows || []).some((r) => !isWithdrawn(r.KOSMAZ) && programRace.runners.some((p) => p.number === Number(r.S1) && /\(ko[şs]maz\)/iu.test(p.rawName)))) reasons.push('SCRATCH_STATUS_CONFLICT');
    if (feed.race.SAAT && programRace.time !== feed.race.SAAT.replace('.', ':')) reasons.push('RACE_TIME_MISMATCH');
    if (feed.race.PIST && programRace.surface !== feed.race.PIST) reasons.push('RACE_SURFACE_MISMATCH');
  }
  if (feed.race.DURUM !== 'AÇIK' || info.DURUM !== 'AÇIK') reasons.push('RACE_NOT_OPEN');
  return [...new Set(reasons)];
}
