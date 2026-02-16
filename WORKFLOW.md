# Git Workflow Guide

## 🔄 Pull Request Workflow

### **Recommended Development Process:**

1. **Create Feature Branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make Changes**
   - Work on your feature branch
   - Test locally
   - Commit changes with descriptive messages

3. **Push Feature Branch**
   ```bash
   git push -u origin feature/your-feature-name
   ```

4. **Create Pull Request**
   - GitHub will show PR link in terminal output
   - Or visit: https://github.com/jainnalin/travel-agent/pulls
   - Add description, reviewers, labels

5. **Review and Merge**
   - Review changes in PR
   - Merge to main via GitHub UI or command line
   - Delete feature branch after merge

### **Current Branch Status:**
- ✅ **Main**: `main` - Stable production branch
- 🚧 **Feature**: `feature/budget-filtering-fix` - Current work branch

### **Benefits of PR Workflow:**
- 🔍 **Code Review**: Every change gets reviewed
- 🛡️ **Safety**: Main branch stays stable
- 📊 **Tracking**: PR tab shows all pending changes
- 🔄 **Rollback**: Easy to revert if issues found
- 👥 **Collaboration**: Team can review and comment

### **Commands for Current Work:**
```bash
# Switch to feature branch
git checkout feature/budget-filtering-fix

# After making changes
git add .
git commit -m "Your descriptive message"
git push -u origin feature/budget-filtering-fix

# When ready to merge
git checkout main
git pull origin main
git merge feature/budget-filtering-fix
git push origin main
git branch -d feature/budget-filtering-fix
```

### **PR Creation Link:**
https://github.com/jainnalin/travel-agent/pull/new/feature/budget-filtering-fix

**🎯 This workflow ensures every change is reviewed before hitting main branch!**
