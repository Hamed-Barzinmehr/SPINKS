#!/bin/bash
#PBS -N Mn_by_UHF_UNO_HS
#PBS -l nodes=1:ppn=12
#PBS -o Mn_by_UHF_UNO_HS.pbs.out
#PBS -e Mn_by_UHF_UNO_HS.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Mn_by_UHF_UNO_HS"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Mn_by_UHF_UNO_HS.inp > Mn_by_UHF_UNO_HS.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
