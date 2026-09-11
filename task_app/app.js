'use strict';

// ---------- Storage helper (ES6+) ----------
const STORAGE_KEY = 'task-app:tasks.v1';

const saveTasks = tasks => localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));

const loadTasks = () => {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) ?? []; }
  catch { return []; }
};

// ---------- State + refs (destructuring) ----------
let allTasks = loadTasks();
let currentFilter = 'all';
let searchTerm = '';

const taskForm     = document.getElementById('taskForm');
const taskInput    = document.getElementById('taskInput');
const errorMsg     = document.getElementById('error-msg');
const searchInput  = document.getElementById('searchInput');
const filterButtons = [...document.querySelectorAll('.filter-btn')];
const taskList     = document.getElementById('taskList');

let nextId = (allTasks.map(t => t.id).reduce((max, id) => Math.max(max, id), 0)) + 1;

// ---------- Core actions ----------
function addTask({ target }) {
  const value = taskInput.value.trim();
  if (!value) { showError('Please enter a task.'); return; }

  errorMsg.hidden = true;
  allTasks.push({ id: nextId++, text: value, completed: false });
  saveTasks(allTasks);
  target.reset();
  render();
}

function toggleTask(e) {
  const li = e.target.closest('.task-item');
  if (!li) return;
  const id = Number(li.dataset.id);
  allTasks = allTasks.map(t => (t.id === id ? { ...t, completed: !t.completed } : t));
  saveTasks(allTasks);
  render();
}

function deleteTask(e) {
  const li = e.target.closest('.task-item');
  if (!li) return;
  const id = Number(li.dataset.id);
  allTasks = allTasks.filter(t => t.id !== id);
  saveTasks(allTasks);
  render();
}

function showError(msg) { errorMsg.textContent = msg; errorMsg.hidden = false; }

// ---------- Filtering + search ----------
const isMatch = task => {
  const needle = searchTerm.trim().toLowerCase();
  if (needle && !task.text.toLowerCase().includes(needle)) return false;
  return currentFilter === 'completed' ? task.completed
       : currentFilter === 'active'      ? !task.completed
       : true;
};

function applyFilter({ target }) {
  const selectedBtn = target.closest('.filter-btn');
  if (!selectedBtn) return;
  currentFilter = selectedBtn.dataset.filter;
  filterButtons.forEach(btn => btn.classList.toggle('active', btn === selectedBtn));
  render();
}

// ---------- Rendering (arrow + createElement) ----------
function renderTaskItem({ id, text, completed }) {
  const li = document.createElement('li');
  li.className = 'task-item' + (completed ? ' completed' : '');
  li.dataset.id = String(id);

  const label   = document.createElement('label');
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = completed;
  label.append(checkbox, text);

  const delBtn = document.createElement('button');
  delBtn.className = 'task-item-actions';
  delBtn.textContent = '🗑️';

  li.append(label, delBtn);
  return li;
}

function render() {
  taskList.innerHTML = '';

  if (!allTasks.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = searchTerm.trim().length ? '🔍 No results.' : '✅ No tasks yet. Add one below!';
    taskList.append(empty);
  }

  allTasks.filter(isMatch).slice().reverse().forEach(item => {
    const li = renderTaskItem(item);
    taskList.append(li);
  });
}

// ---------- Event listeners (single delegation points) ----------
taskForm.addEventListener('submit', addTask);
searchInput.addEventListener('input', ({ target: { value } }) => { searchTerm = value; render(); });
filterButtons.forEach(btn => btn.addEventListener('click', applyFilter));
taskList.addEventListener('change', toggleTask);
taskList.addEventListener('click', deleteTask);

// ---------- Initial paint ----------
render();
