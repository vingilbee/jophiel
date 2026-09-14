import numpy as np

# Impression Updater Using Bayes Gausian updating
class ImpressionUpdater:
    def __init__(self, mu0 = 0.0, sigma0=1):
        self.mu = mu0 # mean
        self.sigma2 = sigma0**2 # belief variance

    def update(self, x, bias, confidence):
        sigma_x = self.compute_sigma_x(x, bias, confidence)
        sigma_x2 = sigma_x**2
        # Param x = cues of the traits
        w = self.sigma2 / (self.sigma2 + sigma_x2)
        w *= (1 + bias["trust_bias"])
        if x < 0:
            w *= (1 + bias["negativity_bias"])
        self.mu = w * x + (1 - w) * self.mu
        self.sigma2 = (self.sigma2 * sigma_x2) / (self.sigma2 + sigma_x2)

        return self.mu, self.sigma2

    def compute_sigma_x(self, e_raw, bias, confidence):
      ambiguity = 1 - confidence
      strength = 1 - abs(e_raw)

      sigma_x = (
          0.2
          + 0.6 * ambiguity
          + 0.4 * strength
      )

      if e_raw < 0:
        sigma_x *= (1 + bias["negativity_bias"])
      if abs(e_raw) > 0.7:
        sigma_x *= 0.7
      return np.clip(sigma_x, 0.05, 2)

class MultiTraitUpdater:
    def __init__(self, impression):
        self.traits = {
            name: ImpressionUpdater(impression[name][0], impression[name][1])
            for name in impression.keys()
        }

    def update(self, cue, bias, confidence):
        # param cue a dictionary of traits with cue weights
        result = {}
        for t_name, x in cue.items():
            mu, sigma2 = self.traits[t_name].update(x, bias, confidence[t_name])
            result[t_name] = (float(mu), float(sigma2))
        return result


