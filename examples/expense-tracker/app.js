"use strict";

const STORAGE_KEY = "expense-tracker.expenses.v1";

const seedExpenses = [
  { id: "seed-1", description: "Weekly groceries", amount: 48.2, category: "Food", date: todayIso(-3) },
  { id: "seed-2", description: "Train to office", amount: 9.6, category: "Transport", date: todayIso(-2) },
  { id: "seed-3", description: "Cinema night", amount: 24.0, category: "Entertainment", date: todayIso(-1) },
];

const state = {
  expenses: load(),
  filter: "All",
};

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (_) {
    /* fall through to seed */
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(seedExpenses));
  return [...seedExpenses];
}

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.expenses));
}

function todayIso(daysAgo) {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

function money(amount) {
  return new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" }).format(amount);
}

function renderSummary() {
  const now = new Date();
  const month = now.toISOString().slice(0, 7);
  const monthExpenses = state.expenses.filter((e) => e.date.startsWith(month));
  const total = state.expenses.reduce((sum, e) => sum + Number(e.amount), 0);
  const monthTotal = monthExpenses.reduce((sum, e) => sum + Number(e.amount), 0);
  document.getElementById("total").textContent = money(total);
  document.getElementById("month-total").textContent = money(monthTotal);
  document.getElementById("count").textContent = String(state.expenses.length);
}

function renderList() {
  const list = document.getElementById("expense-list");
  const empty = document.getElementById("empty-state");
  const visible = state.expenses
    .filter((e) => state.filter === "All" || e.category === state.filter)
    .sort((a, b) => b.date.localeCompare(a.date));

  list.innerHTML = "";
  empty.hidden = visible.length > 0;

  for (const expense of visible) {
    const li = document.createElement("li");
    li.className = "expense-item";

    const details = document.createElement("div");
    details.className = "expense-details";

    const description = document.createElement("span");
    description.className = "expense-description";
    description.textContent = expense.description;

    const meta = document.createElement("span");
    meta.className = "expense-meta";
    meta.textContent = `${expense.category} · ${expense.date}`;

    details.append(description, meta);

    const amount = document.createElement("span");
    amount.className = "expense-amount";
    amount.textContent = money(expense.amount);

    const remove = document.createElement("button");
    remove.className = "btn remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Delete ${expense.description}`);
    remove.addEventListener("click", () => {
      state.expenses = state.expenses.filter((e) => e.id !== expense.id);
      save();
      render();
    });

    li.append(details, amount, remove);
    list.appendChild(li);
  }
}

function render() {
  renderSummary();
  renderList();
}

document.getElementById("expense-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const description = document.getElementById("description").value.trim();
  const amount = Number(document.getElementById("amount").value);
  const category = document.getElementById("category").value;
  const date = document.getElementById("date").value || todayIso(0);
  if (!description || !(amount > 0)) return;
  state.expenses.push({
    id: `e-${Date.now()}`,
    description,
    amount,
    category,
    date,
  });
  save();
  event.target.reset();
  document.getElementById("date").value = todayIso(0);
  render();
});

document.getElementById("filter").addEventListener("change", (event) => {
  state.filter = event.target.value;
  renderList();
});

document.getElementById("export-btn").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(state.expenses, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "expenses.json";
  a.click();
  URL.revokeObjectURL(url);
});

document.getElementById("import-btn").addEventListener("click", () => {
  document.getElementById("import-file").click();
});

document.getElementById("import-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const imported = JSON.parse(String(reader.result));
      if (!Array.isArray(imported)) throw new Error("not an array");
      state.expenses = [...state.expenses, ...imported];
      save();
      render();
    } catch (_) {
      alert("That file does not look like an expense export.");
    }
  };
  reader.readAsText(file);
  event.target.value = "";
});

document.getElementById("date").value = todayIso(0);
render();
