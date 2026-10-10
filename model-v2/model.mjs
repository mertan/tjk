/** Conditional-logit (per-race softmax) winner model trained with Adam + L2. */
export function scores(weights, X) {
  return X.map((row) => row.reduce((sum, value, j) => sum + value * weights[j], 0));
}

export function softmax(values) {
  const max = Math.max(...values);
  const exp = values.map((value) => Math.exp(value - max));
  const total = exp.reduce((a, b) => a + b, 0);
  return exp.map((value) => value / total);
}

const winnerIndex = (example) => {
  const index = example.positions.indexOf(1);
  return index >= 0 && example.positions.filter((p) => p === 1).length === 1 ? index : -1;
};

export function logLoss(weights, examples) {
  let total = 0;
  let count = 0;
  for (const example of examples) {
    const win = winnerIndex(example);
    if (win < 0) continue;
    total -= Math.log(Math.max(1e-12, softmax(scores(weights, example.X))[win]));
    count += 1;
  }
  return count ? total / count : null;
}

export function train(examples, { l2 = 1, epochs = 400, lr = 0.05 } = {}) {
  const usable = examples.filter((example) => winnerIndex(example) >= 0);
  const dim = usable[0]?.X[0]?.length || 0;
  const weights = new Array(dim).fill(0);
  const m = new Array(dim).fill(0);
  const v = new Array(dim).fill(0);
  for (let epoch = 1; epoch <= epochs; epoch += 1) {
    const grad = weights.map((w) => (l2 * w) / usable.length);
    for (const example of usable) {
      const p = softmax(scores(weights, example.X));
      const win = winnerIndex(example);
      example.X.forEach((row, i) => {
        const coef = (p[i] - (i === win ? 1 : 0)) / usable.length;
        for (let j = 0; j < dim; j += 1) grad[j] += coef * row[j];
      });
    }
    for (let j = 0; j < dim; j += 1) {
      m[j] = 0.9 * m[j] + 0.1 * grad[j];
      v[j] = 0.999 * v[j] + 0.001 * grad[j] ** 2;
      weights[j] -= (lr * (m[j] / (1 - 0.9 ** epoch))) / (Math.sqrt(v[j] / (1 - 0.999 ** epoch)) + 1e-8);
    }
  }
  return weights;
}

export function predict(weights, example) {
  const probabilities = softmax(scores(weights, example.X));
  const leader = probabilities.indexOf(Math.max(...probabilities));
  return { leader: example.numbers[leader], leaderIndex: leader, probabilities };
}
