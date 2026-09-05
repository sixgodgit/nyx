<script setup>
import { icon } from '../icons.js'
defineProps({ name: { type: String, required: true }, size: { type: Number, default: 18 } })
</script>
<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
    aria-hidden="true" v-html="icon(name)"></svg>
</template>
