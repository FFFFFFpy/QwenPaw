import type { ProviderInfo } from "../../../api/types/provider";

/** Determine if a provider has valid credentials configured. */
export function getIsConfigured(provider: ProviderInfo): boolean {
  if (provider.id === "qwenpaw-local") return true;
  if (provider.meta?.provider_kind === "cloud_subscription") {
    return provider.oauth_connected === true;
  }
  if (provider.is_custom && provider.base_url) return true;
  if (provider.require_api_key === false) return true;
  if (provider.require_api_key && provider.api_key) return true;
  return false;
}

export function countConfiguredProviders(providers: ProviderInfo[]): number {
  return providers.filter(getIsConfigured).length;
}

/** Determine which settings section owns a provider. */
export function isProviderReady(provider: ProviderInfo): boolean {
  const hasModels = provider.models.length + provider.extra_models.length > 0;
  if (provider.is_local) {
    return hasModels || getIsConfigured(provider);
  }
  return getIsConfigured(provider);
}

/** Split providers by location and configuration state. */
export function partitionProviders(providers: ProviderInfo[]): {
  localConfigured: ProviderInfo[];
  localAvailable: ProviderInfo[];
  cloudConfigured: ProviderInfo[];
  cloudAvailable: ProviderInfo[];
} {
  const localConfigured: ProviderInfo[] = [];
  const localAvailable: ProviderInfo[] = [];
  const cloudConfigured: ProviderInfo[] = [];
  const cloudAvailable: ProviderInfo[] = [];
  const isEmbedded = (provider: ProviderInfo) =>
    provider.id === "qwenpaw-local" || provider.id === "copaw-local";

  const cloud: ProviderInfo[] = [];
  for (const provider of providers) {
    if (provider.is_local || provider.is_custom) {
      if (isEmbedded(provider) || isProviderReady(provider)) {
        localConfigured.push(provider);
      } else {
        localAvailable.push(provider);
      }
    } else {
      cloud.push(provider);
    }
  }

  // API-key variants remain grouped when any sibling is configured. Cloud
  // subscriptions are deliberately ungrouped by groupProviders(), so their
  // OAuth state alone controls their section.
  const configuredGroups = new Set<string>();
  for (const provider of cloud) {
    if (
      provider.meta?.provider_kind !== "cloud_subscription" &&
      provider.provider_group &&
      isProviderReady(provider)
    ) {
      configuredGroups.add(provider.provider_group);
    }
  }
  for (const provider of cloud) {
    const isSubscription =
      provider.meta?.provider_kind === "cloud_subscription";
    const ready = isProviderReady(provider);
    if (
      ready ||
      (!isSubscription &&
        !!provider.provider_group &&
        configuredGroups.has(provider.provider_group))
    ) {
      cloudConfigured.push(provider);
    } else {
      cloudAvailable.push(provider);
    }
  }

  return {
    localConfigured,
    localAvailable,
    cloudConfigured,
    cloudAvailable,
  };
}

export interface ProviderGroup {
  groupKey: string;
  groupName: string;
  providers: ProviderInfo[];
}

/**
 * Split providers into grouped (same brand) and ungrouped lists.
 */
export function groupProviders(providers: ProviderInfo[]): {
  grouped: ProviderGroup[];
  ungrouped: ProviderInfo[];
} {
  const groupMap = new Map<string, ProviderGroup>();
  const ungrouped: ProviderInfo[] = [];

  for (const p of providers) {
    if (p.meta?.provider_kind === "cloud_subscription") {
      ungrouped.push(p);
      continue;
    }
    if (p.provider_group) {
      const existing = groupMap.get(p.provider_group);
      if (existing) {
        existing.providers.push(p);
      } else {
        groupMap.set(p.provider_group, {
          groupKey: p.provider_group,
          groupName: p.provider_group_name || p.provider_group,
          providers: [p],
        });
      }
    } else {
      ungrouped.push(p);
    }
  }

  const grouped: ProviderGroup[] = [];
  for (const g of groupMap.values()) {
    if (g.providers.length >= 2) {
      grouped.push(g);
    } else {
      ungrouped.push(...g.providers);
    }
  }

  return { grouped, ungrouped };
}
