import { defineCollection, z } from 'astro:content';

const restaurants = defineCollection({
  type: 'content',
  schema: z.object({
    name: z.string(),
    status: z.enum(['active', 'closed', 'coming_soon']).default('active'),

    // Classification
    cuisine: z.array(z.string()),
    neighborhood: z.string(),
    price: z.enum(['$', '$$', '$$$', '$$$$']),
    ownership: z.enum(['independent', 'local_group', 'regional_chain', 'national_chain']).default('independent'),

    // Location
    address: z.string(),
    phone: z.string().optional(),
    website: z.string().optional(),
    reservations: z.enum(['opentable', 'resy', 'phone', 'walk-in', 'none']).optional(),

    // Operations
    hours: z.record(z.string()).optional(),
    byob: z.boolean().default(false),
    outdoor_seating: z.boolean().default(false),

    // Tags
    good_for: z.array(z.string()).default([]),
    vibe: z.array(z.string()).default([]),

    // Ratings (1–10, editorial, filled in post-visit)
    ratings: z.object({
      consistency: z.number().min(1).max(10).optional(),
      distinctiveness: z.number().min(1).max(10).optional(),
      execution: z.number().min(1).max(10).optional(),
      local_relevance: z.number().min(1).max(10).optional(),
    }).optional(),

    // What to Order
    dishes: z.array(z.object({
      name: z.string(),
      take: z.string(),
    })).default([]),

    // Practical
    parking: z.string().optional(),
    noise_level: z.string().optional(),
    wait_times: z.string().optional(),

    // Media
    image: z.string().default(''),
    image_alt: z.string().default(''),

    // Awards
    awards: z.array(z.object({
      name: z.string(),
      year: z.number(),
      category: z.string().optional(),
    })).default([]),

    // Editorial metadata
    one_liner: z.string(),
    visit_status: z.enum(['visited', 'pending', 'fact-shell']).default('fact-shell'),
    last_visited: z.string().optional(),
    last_updated: z.string(),
    published: z.boolean().default(false),
    year_established: z.number().optional(),
    owner_names: z.array(z.string()).default([]),
    awards_note: z.string().optional(),
  }),
});

export const collections = { restaurants };